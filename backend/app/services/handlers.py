"""Job handlers. Imported by the job runner so each registers itself."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pos.base import POSAuthError
from app.integrations.pos.registry import POS_PROVIDERS
from app.models import Integration, Location, Review, WebhookEvent, utcnow
from app.services import alerts, notify, reviews, sync
from app.services.jobs import enqueue, handler


@handler("pos_webhook")
async def handle_pos_webhook(session: AsyncSession, payload: dict) -> None:
    event = await session.get(WebhookEvent, payload["event_id"])
    if event is None or event.processed_at is not None:
        return
    await sync.apply_webhook(session, event.provider, event.body)
    event.processed_at = utcnow()
    await session.commit()


@handler("sync_pos")
async def handle_sync_pos(session: AsyncSession, payload: dict) -> None:
    integ = await session.get(Integration, payload["integration_id"])
    if integ is None or integ.status == "disconnected":
        return
    try:
        await sync.sync_pos(session, integ, full=bool(payload.get("full")))
    except POSAuthError:
        return  # integration already marked broken and the owner alerted


@handler("poll_reviews")
async def handle_poll_reviews(session: AsyncSession, payload: dict) -> None:
    integ = await session.get(Integration, payload["integration_id"])
    if integ is None or integ.status == "disconnected":
        return
    try:
        if integ.provider == "google":
            await reviews.poll_google(session, integ)
    except POSAuthError:
        return


@handler("triage_review")
async def handle_triage_review(session: AsyncSession, payload: dict) -> None:
    from app.agent.triage import triage_review

    review = await session.get(Review, payload["review_id"])
    if review is None:
        return
    if review.triaged_at is None:
        await triage_review(session, review)
    if payload.get("alert", True):
        location = await session.get(Location, review.location_id)
        await alerts.alert_for_review(session, location, review)
    await session.commit()


@handler("deliver_alert")
async def handle_deliver_alert(session: AsyncSession, payload: dict) -> None:
    await notify.deliver_alert(session, payload["alert_id"])


@handler("daily_digest")
async def handle_daily_digest(session: AsyncSession, payload: dict) -> None:
    location = await session.get(Location, payload["location_id"])
    if location is not None:
        await alerts.send_daily_digest(session, location)


@handler("tick")
async def handle_tick(session: AsyncSession, payload: dict) -> None:
    await schedule_periodic(session)


async def schedule_periodic(session: AsyncSession) -> dict:
    """Fan out periodic work for every location. Called every 15 minutes by Cloud Scheduler."""
    now = utcnow()
    slot = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).isoformat()
    counts = {"sync_pos": 0, "poll_reviews": 0, "daily_digest": 0}
    integrations = list(await session.scalars(select(Integration).where(Integration.status == "active")))
    for i, integ in enumerate(integrations):
        # Spread calls over the window so we don't hit a provider with everything at once.
        run_at = now + timedelta(seconds=(i * 7) % 600)
        if integ.provider in POS_PROVIDERS:
            if await enqueue(session, "sync_pos", {"integration_id": integ.id}, run_at=run_at,
                             dedupe_key=f"sync_pos:{integ.id}"):
                counts["sync_pos"] += 1
        elif integ.provider == "google":
            if await enqueue(session, "poll_reviews", {"integration_id": integ.id}, run_at=run_at,
                             dedupe_key=f"poll_reviews:{integ.id}"):
                counts["poll_reviews"] += 1
    for location in await session.scalars(select(Location)):
        local = now.astimezone(ZoneInfo(location.timezone))
        if local.hour == location.digest_hour and local.minute < 15:
            if await enqueue(session, "daily_digest", {"location_id": location.id},
                             dedupe_key=f"digest:{location.id}:{local.date().isoformat()}"):
                counts["daily_digest"] += 1
    await session.commit()
    return {"slot": slot, **counts}
