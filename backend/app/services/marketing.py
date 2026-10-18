"""Facts the Restaurant Manager agent turns into a marketing plan: slow times, items on the rise,
upcoming special days, the guest list (consented contacts only) and what reviewers mention."""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySales, Guest, ItemSalesDaily, Location, Review, utcnow
from app.money import fmt_money
from app.services import holidays
from app.services.analytics import WEEKDAYS, local_today


async def insights(session: AsyncSession, location: Location) -> dict:
    today = local_today(location)
    start = today - timedelta(days=28)
    days = list(await session.scalars(
        select(DailySales).where(DailySales.location_id == location.id, DailySales.day >= start,
                                 DailySales.day < today)))
    # Average sales per weekday and per weekday-hour over the last 4 weeks.
    per_day: dict[int, list[int]] = defaultdict(list)
    per_slot: dict[tuple[int, int], list[int]] = defaultdict(list)
    for d in days:
        per_day[d.day.weekday()].append(d.gross_cents)
        for h, cents in (d.by_hour or {}).items():
            per_slot[(d.day.weekday(), int(h))].append(cents)
    weekday_avg = {w: sum(v) / len(v) for w, v in per_day.items() if v}
    slow_days = [{"weekday": WEEKDAYS[w], "avg_sales": fmt_money(round(a), location.currency)}
                 for w, a in sorted(weekday_avg.items(), key=lambda x: x[1])[:2]]
    # Slow hours: open hours (sales on most of those weekdays) earning least compared to that day's peak hour.
    slots = []
    for (w, h), values in per_slot.items():
        if len(values) < max(2, len(per_day.get(w, [])) // 2):
            continue
        avg = sum(values) / len(per_day[w])
        peak = max(sum(v) / len(per_day[w]) for (w2, _), v in per_slot.items() if w2 == w)
        slots.append((avg / peak if peak else 1, w, h, avg))
    slow_slots = [{"weekday": WEEKDAYS[w], "hour": f"{h:02d}:00", "avg_sales": fmt_money(round(a), location.currency),
                   "pct_of_peak_hour": round(r * 100)} for r, w, h, a in sorted(slots)[:4]]

    # Items rising or falling: last 14 days vs the 14 before, by quantity.
    rows = await session.execute(
        select(ItemSalesDaily.name, ItemSalesDaily.day, ItemSalesDaily.qty)
        .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day >= start, ItemSalesDaily.day < today))
    recent, before = Counter(), Counter()
    for name, day, qty in rows:
        (recent if day >= today - timedelta(days=14) else before)[name] += float(qty or 0)
    moves = [(name, recent[name], before[name]) for name in set(recent) | set(before) if before[name] >= 5]
    moves.sort(key=lambda m: (m[1] - m[2]) / m[2], reverse=True)
    def fmt(m):
        return {"name": m[0], "last_14_days": round(m[1]), "previous_14_days": round(m[2]),
                "change_pct": round((m[1] - m[2]) / m[2] * 100)}
    rising = [fmt(m) for m in moves[:3] if m[1] > m[2]]
    falling = [fmt(m) for m in reversed(moves[-3:]) if m[1] < m[2]]

    upcoming = [{"day": d.isoformat(), "name": n, "kind": k}
                for d, (n, k) in holidays.days_between(today, today + timedelta(days=30)).items()]

    now = utcnow()
    consented = await session.scalar(select(func.count(Guest.id)).where(
        Guest.location_id == location.id, Guest.marketing_consent.is_(True))) or 0
    lapsed = await session.scalar(select(func.count(Guest.id)).where(
        Guest.location_id == location.id, Guest.marketing_consent.is_(True),
        Guest.last_visit_at < now - timedelta(days=45))) or 0
    regulars = await session.scalar(select(func.count(Guest.id)).where(
        Guest.location_id == location.id, Guest.visits >= 5)) or 0

    topics = Counter()
    praise = Counter()
    for r in await session.scalars(select(Review).where(
            Review.location_id == location.id, Review.posted_at >= now - timedelta(days=90))):
        for t in r.topics or []:
            (praise if r.rating >= 4 else topics)[t] += 1
    return {
        "slow_days": slow_days,
        "slow_hours": slow_slots,
        "rising_items": rising,
        "falling_items": falling,
        "upcoming_days": upcoming,
        "guests": {"marketing_consent": consented, "lapsed_45_days_with_consent": lapsed,
                   "regulars_5_plus_visits": regulars},
        "review_topics": {"complaints": dict(topics.most_common(4)), "praise": dict(praise.most_common(4))},
        "rules": "Only guests with marketing consent may receive promotional emails or texts (CASL); every message "
        "needs an unsubscribe option.",
    }
