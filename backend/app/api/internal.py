"""Endpoints for Cloud Scheduler and operators. Protected by a shared token (and Cloud Run IAM in production)."""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Job
from app.services.handlers import schedule_periodic
from app.services.jobs import run_pending

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_token
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(403, "Forbidden")


@router.post("/tick", dependencies=[Depends(require_internal)])
async def tick(session: AsyncSession = Depends(get_session)):
    """Every 15 minutes: queue POS syncs, review polls and due digests."""
    return await schedule_periodic(session)


@router.post("/run-jobs", dependencies=[Depends(require_internal)])
async def run_jobs(max_jobs: int = 200, time_budget_s: float = 50.0):
    """Every minute (or run the worker process instead): drain ready jobs."""
    return {"ran": await run_pending(max_jobs=max_jobs, time_budget_s=time_budget_s)}


@router.get("/queue", dependencies=[Depends(require_internal)])
async def queue_stats(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
    return {status: count for status, count in rows}
