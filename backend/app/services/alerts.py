"""Create alerts (deduplicated), evaluate alert rules, and build the daily digest."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertRule, DailySales, Item, Location, Review, utcnow
from app.money import fmt_money, pct_change
from app.services.jobs import enqueue

DEFAULT_CHANNELS = ["push", "email"]


async def rule_for(session: AsyncSession, location: Location, kind: str) -> AlertRule | None:
    return await session.scalar(
        select(AlertRule).where(AlertRule.location_id == location.id, AlertRule.kind == kind)
    )


async def create_alert(
    session: AsyncSession,
    location: Location,
    *,
    kind: str,
    title: str,
    body: str,
    dedupe_key: str,
    data: dict | None = None,
    channels: list[str] | None = None,
) -> Alert | None:
    """Insert an alert unless one with the same dedupe key exists, then queue its delivery."""
    exists = await session.scalar(
        select(Alert.id).where(Alert.location_id == location.id, Alert.dedupe_key == dedupe_key)
    )
    if exists:
        return None
    if channels is None:
        rule = await rule_for(session, location, kind)
        if rule is not None and not rule.enabled:
            return None
        channels = list(rule.channels) if rule else list(DEFAULT_CHANNELS)
    alert = Alert(
        location_id=location.id, kind=kind, title=title, body=body, data=data or {},
        channels=channels, dedupe_key=dedupe_key,
    )
    session.add(alert)
    await session.flush()
    await enqueue(session, "deliver_alert", {"alert_id": alert.id})
    return alert


async def check_low_stock(session: AsyncSession, location: Location) -> int:
    items = list(
        await session.scalars(
            select(Item).where(
                Item.location_id == location.id,
                Item.active.is_(True),
                Item.stock_qty.is_not(None),
                Item.low_stock_threshold.is_not(None),
                Item.stock_qty <= Item.low_stock_threshold,
            )
        )
    )
    created = 0
    today = utcnow().date().isoformat()
    for item in items:
        alert = await create_alert(
            session,
            location,
            kind="low_stock",
            title=f"Low stock: {item.name}",
            body=f"{item.name} is down to {item.stock_qty:g}{(' ' + item.unit) if item.unit else ''} "
            f"(alert level {item.low_stock_threshold:g}).",
            data={"item_id": item.id},
            dedupe_key=f"low_stock:{item.id}:{today}",  # at most once a day per item
        )
        created += alert is not None
    await session.commit()
    return created


async def alert_for_review(session: AsyncSession, location: Location, review: Review) -> Alert | None:
    if review.rating > 3 and not review.urgent:
        return None
    stars = "★" * review.rating + "☆" * (5 - review.rating)
    snippet = (review.text or "(no text)")[:240]
    return await create_alert(
        session,
        location,
        kind="bad_review",
        title=f"New {review.rating}-star {review.source.title()} review",
        body=f"{stars} from {review.author or 'a customer'}: \"{snippet}\"",
        data={"review_id": review.id},
        dedupe_key=f"review:{review.id}",
    )


async def check_sales_drop(session: AsyncSession, location: Location, threshold_pct: float = -25.0) -> Alert | None:
    """Compare yesterday with the average of the same weekday over the previous four weeks."""
    from app.services.analytics import local_today

    yesterday = local_today(location) - timedelta(days=1)
    rows = {
        r.day: r
        for r in await session.scalars(
            select(DailySales).where(
                DailySales.location_id == location.id,
                DailySales.day.in_([yesterday - timedelta(days=7 * k) for k in range(0, 5)]),
            )
        )
    }
    if yesterday not in rows:
        return None
    history = [rows[d].gross_cents for d in rows if d != yesterday]
    if len(history) < 2:
        return None
    baseline = sum(history) / len(history)
    change = pct_change(rows[yesterday].gross_cents, baseline)
    rule = await rule_for(session, location, "sales_drop")
    limit = float((rule.params or {}).get("threshold_pct", threshold_pct)) if rule else threshold_pct
    if change is None or change > limit:
        return None
    return await create_alert(
        session,
        location,
        kind="sales_drop",
        title=f"Sales down {abs(change):.0f}% yesterday",
        body=f"Yesterday took {fmt_money(rows[yesterday].gross_cents, location.currency)} vs a usual "
        f"{fmt_money(round(baseline), location.currency)} for a {yesterday.strftime('%A')}.",
        dedupe_key=f"sales_drop:{yesterday.isoformat()}",
    )


async def build_digest(session: AsyncSession, location: Location) -> tuple[str, str]:
    """A plain, numbers-first morning summary. No AI call needed, so it costs nothing."""
    from app.services import analytics

    s = await analytics.sales_summary(session, location, "yesterday", "same_weekday_last_week")
    top = await analytics.top_items(session, location, "yesterday", limit=3)
    low = await analytics.inventory(session, location, low_stock_only=True, limit=5)
    reviews = await analytics.list_reviews(session, location, "yesterday", limit=5)
    cmp = s.get("comparison") or {}
    change = cmp.get("gross_change_pct")
    lines = [
        f"Yesterday: {s['gross']} from {s['orders']} orders (avg ticket {s['avg_ticket']})"
        + (f", {change:+.0f}% vs last week" if change is not None else "") + ".",
    ]
    if top["rows"]:
        lines.append("Top sellers: " + ", ".join(f"{r['name']} ({r['qty']:g})" for r in top["rows"]) + ".")
    if low["items"]:
        lines.append("Running low: " + ", ".join(i["name"] for i in low["items"]) + ".")
    if reviews["reviews"]:
        lines.append(
            f"{len(reviews['reviews'])} new review(s), average {reviews['average_rating']}★."
        )
    title = f"{location.name}: yesterday {s['gross']}"
    return title, "\n".join(lines)


async def send_daily_digest(session: AsyncSession, location: Location) -> Alert | None:
    from app.services.analytics import local_today

    title, body = await build_digest(session, location)
    alert = await create_alert(
        session, location, kind="daily_digest", title=title, body=body,
        dedupe_key=f"digest:{local_today(location).isoformat()}",
        channels=None,
    )
    await check_sales_drop(session, location)
    await session.commit()
    return alert
