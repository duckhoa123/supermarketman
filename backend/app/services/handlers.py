"""Job handlers. Imported by the job runner so each registers itself."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pos.base import POSAuthError
from app.integrations.pos.registry import POS_PROVIDERS
from app.models import Integration, Location, Review, WebhookEvent, utcnow
from app.services import alerts, fleet, notify, purchasing, receiving, reports, reviews, sync, weather  # noqa: F401
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


@handler("period_report")
async def handle_period_report(session: AsyncSession, payload: dict) -> None:
    location = await session.get(Location, payload["location_id"])
    if location is None:
        return
    kind = payload["kind"]
    title, body = reports.render(await reports.report(session, location, kind))
    await alerts.create_alert(session, location, kind=f"{kind}ly_report", title=title, body=body,
                              dedupe_key=f"{kind}_report:{payload['period_key']}")
    await session.commit()


@handler("refresh_weather")
async def handle_refresh_weather(session: AsyncSession, payload: dict) -> None:
    location = await session.get(Location, payload["location_id"])
    if location is not None:
        await weather.refresh_weather(session, location)


@handler("fleet_sweep")
async def handle_fleet_sweep(session: AsyncSession, payload: dict) -> None:
    await fleet.sweep(session)
    await session.commit()


@handler("tick")
async def handle_tick(session: AsyncSession, payload: dict) -> None:
    await schedule_periodic(session)


async def schedule_periodic(session: AsyncSession) -> dict:
    """Fan out periodic work for every location. Called every 15 minutes by Cloud Scheduler."""
    now = utcnow()
    slot = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).isoformat()
    counts = {"sync_pos": 0, "poll_reviews": 0, "daily_digest": 0, "reports": 0, "weather": 0}
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
            # Weekly report on Mondays and monthly report on the 1st, with the morning digest.
            periods = []
            if local.weekday() == 0:
                periods.append(("week", f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"))
            if local.day == 1:
                periods.append(("month", f"{local.year}-{local.month:02d}"))
            for kind, key in periods:
                if await enqueue(session, "period_report", {"location_id": location.id, "kind": kind,
                                                            "period_key": key},
                                 dedupe_key=f"report:{location.id}:{kind}:{key}"):
                    counts["reports"] += 1
        if local.hour == 5 and local.minute < 15 and location.lat is not None:
            if await enqueue(session, "refresh_weather", {"location_id": location.id},
                             dedupe_key=f"weather:{location.id}:{local.date().isoformat()}"):
                counts["weather"] += 1
    await enqueue(session, "fleet_sweep", {}, dedupe_key="fleet_sweep")
    if now.hour == 10 and now.minute < 15:  # once a day: drop invoice images past their keep period
        await enqueue(session, "purge_invoice_files", {}, dedupe_key=f"purge_invoices:{now.date().isoformat()}")
    await session.commit()
    return {"slot": slot, **counts}
