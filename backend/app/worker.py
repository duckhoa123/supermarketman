"""Background worker: drains the job queue. In development it also does the scheduler's job.

Run:  python -m app.worker            (loop forever)
      python -m app.worker --once     (drain once and exit; for a Cloud Run job)
"""

import argparse
import asyncio
import logging
import signal
import time

from app.db import sessionmaker
from app.services.handlers import schedule_periodic
from app.services.jobs import run_pending

log = logging.getLogger("worker")
TICK_EVERY_S = 15 * 60
ERROR_BACKOFF_S = 15


async def main(once: bool, schedule: bool) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    last_tick = 0.0
    while not stop.is_set():
        ran = 0
        try:
            if schedule and time.monotonic() - last_tick > TICK_EVERY_S:
                async with sessionmaker()() as session:
                    log.info("tick: %s", await schedule_periodic(session))
                last_tick = time.monotonic()
            ran = await run_pending(max_jobs=500, time_budget_s=55)
            if ran:
                log.info("ran %d job(s)", ran)
        except Exception:  # noqa: BLE001 - e.g. database not migrated yet or briefly unreachable: retry, don't crash
            log.exception("worker loop failed; retrying in %ss", ERROR_BACKOFF_S)
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=ERROR_BACKOFF_S)
            except TimeoutError:
                pass
            continue
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=2 if ran else 5)
        except TimeoutError:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--schedule", action="store_true", help="also queue periodic jobs (dev; prod uses Cloud Scheduler)")
    args = parser.parse_args()
    asyncio.run(main(args.once, args.schedule))
