"""A small, durable job queue on Postgres.

- `enqueue` inserts a row; an optional `dedupe_key` stops duplicates while one is still queued.
- `run_pending` claims ready jobs with SELECT ... FOR UPDATE SKIP LOCKED, so several workers can run safely.
- Failures retry with exponential backoff up to `max_attempts`.

At ~200 restaurants this is a few thousand jobs an hour, well within what one Postgres handles.
"""

import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import sessionmaker
from app.models import Job, utcnow

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, dict], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}

STUCK_AFTER = timedelta(minutes=10)


def handler(kind: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


class RetryLater(Exception):
    """Raise from a handler to retry after `delay_s` without counting it as an error (e.g. rate limits)."""

    def __init__(self, delay_s: float):
        super().__init__(f"retry in {delay_s}s")
        self.delay_s = delay_s


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> Job | None:
    if dedupe_key:
        existing = await session.scalar(
            select(Job.id).where(Job.dedupe_key == dedupe_key, Job.status == "queued").limit(1)
        )
        if existing:
            return None
    job = Job(
        kind=kind,
        payload=payload or {},
        run_at=run_at or utcnow(),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    return job


async def _claim(session: AsyncSession, limit: int) -> list[Job]:
    now = utcnow()
    # Recover jobs whose worker died mid-run.
    await session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < now - STUCK_AFTER)
        .values(status="queued", locked_at=None)
    )
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_at <= now)
        .order_by(Job.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list(await session.scalars(stmt))
    for job in jobs:
        job.status = "running"
        job.locked_at = now
        job.attempts += 1
    await session.commit()
    return jobs


async def _run_one(job_id: str) -> None:
    async with sessionmaker()() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        fn = HANDLERS.get(job.kind)
        try:
            if fn is None:
                raise RuntimeError(f"No handler for job kind '{job.kind}'")
            await fn(session, job.payload or {})
            job.status = "done"
            job.finished_at = utcnow()
            job.last_error = None
            await session.commit()
        except RetryLater as exc:
            await session.rollback()
            job = await session.get(Job, job_id)
            job.status = "queued"
            job.attempts = max(0, job.attempts - 1)
            job.run_at = utcnow() + timedelta(seconds=exc.delay_s)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
            await session.rollback()
            job = await session.get(Job, job_id)
            job.last_error = f"{exc}\n{traceback.format_exc()[-2000:]}"
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.finished_at = utcnow()
                log.error("job %s (%s) failed permanently: %s", job.id, job.kind, exc)
            else:
                job.status = "queued"
                job.run_at = utcnow() + timedelta(seconds=min(3600, 15 * 2**job.attempts))
                log.warning("job %s (%s) failed, will retry: %s", job.id, job.kind, exc)
            await session.commit()


async def run_pending(max_jobs: int = 200, time_budget_s: float = 50.0, batch: int = 20) -> int:
    """Run ready jobs until none are left, `max_jobs` ran, or the time budget is spent."""
    # Handlers register themselves on import.
    import app.services.handlers  # noqa: F401

    started = time.monotonic()
    ran = 0
    while ran < max_jobs and time.monotonic() - started < time_budget_s:
        async with sessionmaker()() as session:
            jobs = await _claim(session, min(batch, max_jobs - ran))
        if not jobs:
            break
        for job in jobs:
            await _run_one(job.id)
            ran += 1
    return ran
