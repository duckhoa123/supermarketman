"""Read-side queries shared by the dashboard API and the AI agent's tools.

Every function takes the resolved Location, so callers cannot query another tenant's data.
Results are small, JSON-ready dicts (amounts in cents plus a formatted string) to keep AI prompts short.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySales, DiningTable, Item, ItemSalesDaily, Location, Order, Review, utcnow
from app.money import fmt_money, pct_change, previous_period, resolve_period
from app.services.sync import _day_bounds

MAX_ROWS = 50
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def local_today(location: Location) -> date:
    return utcnow().astimezone(ZoneInfo(location.timezone)).date()


def _range(location: Location, period: str) -> tuple[date, date]:
    return resolve_period(period, local_today(location))


async def _daily(session: AsyncSession, location: Location, start: date, end: date) -> list[DailySales]:
    return list(
        await session.scalars(
            select(DailySales)
            .where(DailySales.location_id == location.id, DailySales.day >= start, DailySales.day <= end)
            .order_by(DailySales.day)
        )
    )


def _totals(rows: list[DailySales]) -> dict:
    gross = sum(r.gross_cents for r in rows)
    orders = sum(r.orders_count for r in rows)
    return {
        "gross_cents": gross,
        "net_cents": gross - sum(r.tax_cents for r in rows),
        "tips_cents": sum(r.tip_cents for r in rows),
        "orders": orders,
        "avg_ticket_cents": round(gross / orders) if orders else 0,
        "days_with_sales": len(rows),
    }


async def sales_summary(
    session: AsyncSession, location: Location, period: str = "today", compare: str | None = "previous"
) -> dict:
    start, end = _range(location, period)
    cur = _totals(await _daily(session, location, start, end))
    out = {
        "period": period,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "currency": location.currency,
        **cur,
        "gross": fmt_money(cur["gross_cents"], location.currency),
        "avg_ticket": fmt_money(cur["avg_ticket_cents"], location.currency),
    }
    if start <= local_today(location) <= end:
        out["note"] = "Period includes today; today's figures are partial."
    if compare:
        ps, pe = previous_period(start, end, compare)
        prev = _totals(await _daily(session, location, ps, pe))
        out["comparison"] = {
            "mode": compare,
            "from": ps.isoformat(),
            "to": pe.isoformat(),
            "gross": fmt_money(prev["gross_cents"], location.currency),
            "orders": prev["orders"],
            "avg_ticket": fmt_money(prev["avg_ticket_cents"], location.currency),
            "gross_change_pct": pct_change(cur["gross_cents"], prev["gross_cents"]),
            "orders_change_pct": pct_change(cur["orders"], prev["orders"]),
        }
    return out


async def sales_breakdown(session: AsyncSession, location: Location, period: str, by: str) -> dict:
    start, end = _range(location, period)
    rows: list[dict] = []
    if by in ("hour", "day", "weekday"):
        daily = await _daily(session, location, start, end)
        if by == "day":
            rows = [
                {"day": r.day.isoformat(), "weekday": WEEKDAYS[r.day.weekday()], "gross_cents": r.gross_cents,
                 "orders": r.orders_count}
                for r in daily
            ]
        elif by == "weekday":
            acc: dict[int, list] = defaultdict(lambda: [0, 0, 0])
            for r in daily:
                a = acc[r.day.weekday()]
                a[0] += r.gross_cents
                a[1] += r.orders_count
                a[2] += 1
            rows = [
                {"weekday": WEEKDAYS[w], "avg_gross_cents": round(a[0] / a[2]), "avg_orders": round(a[1] / a[2], 1)}
                for w, a in sorted(acc.items())
            ]
        else:
            acc_h: dict[int, int] = defaultdict(int)
            for r in daily:
                for h, cents in (r.by_hour or {}).items():
                    acc_h[int(h)] += cents
            days = max(1, len(daily))
            rows = [
                {"hour": f"{h:02d}:00", "gross_cents": c, "avg_per_day_cents": round(c / days)}
                for h, c in sorted(acc_h.items())
            ]
    elif by == "category":
        result = await session.execute(
            select(ItemSalesDaily.category, func.sum(ItemSalesDaily.revenue_cents), func.sum(ItemSalesDaily.qty))
            .where(
                ItemSalesDaily.location_id == location.id,
                ItemSalesDaily.day >= start,
                ItemSalesDaily.day <= end,
            )
            .group_by(ItemSalesDaily.category)
            .order_by(func.sum(ItemSalesDaily.revenue_cents).desc())
        )
        rows = [{"category": c or "Uncategorised", "revenue_cents": int(r or 0), "qty": float(q or 0)}
                for c, r, q in result]
    elif by == "employee":
        s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
        result = await session.execute(
            select(Order.employee_name, func.sum(Order.total_cents), func.count(Order.id), func.sum(Order.tip_cents))
            .where(
                Order.location_id == location.id,
                Order.opened_at >= s,
                Order.opened_at < e,
                Order.status.in_(("paid", "open")),
            )
            .group_by(Order.employee_name)
            .order_by(func.sum(Order.total_cents).desc())
        )
        rows = [
            {"employee": n or "Unassigned", "gross_cents": int(g or 0), "orders": int(c), "tips_cents": int(t or 0)}
            for n, g, c, t in result
        ]
    else:
        raise ValueError("by must be one of hour, day, weekday, category, employee")
    return {"period": period, "from": start.isoformat(), "to": end.isoformat(), "by": by,
            "currency": location.currency, "rows": rows[:MAX_ROWS]}


async def top_items(
    session: AsyncSession, location: Location, period: str, sort_by: str = "revenue", limit: int = 10,
    worst: bool = False,
) -> dict:
    start, end = _range(location, period)
    rev = func.sum(ItemSalesDaily.revenue_cents)
    qty = func.sum(ItemSalesDaily.qty)
    order_col = (qty if sort_by == "qty" else rev)
    result = await session.execute(
        select(ItemSalesDaily.name, ItemSalesDaily.category, rev, qty)
        .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day >= start, ItemSalesDaily.day <= end)
        .group_by(ItemSalesDaily.name, ItemSalesDaily.category)
        .order_by(order_col.asc() if worst else order_col.desc())
        .limit(min(limit, MAX_ROWS))
    )
    rows = [
        {"name": n, "category": c, "revenue_cents": int(r or 0), "revenue": fmt_money(int(r or 0), location.currency),
         "qty": round(float(q or 0), 2)}
        for n, c, r, q in result
    ]
    return {"period": period, "from": start.isoformat(), "to": end.isoformat(), "sort_by": sort_by,
            "order": "lowest first" if worst else "highest first", "rows": rows}


async def inventory(
    session: AsyncSession, location: Location, low_stock_only: bool = False, search: str | None = None,
    limit: int = 50,
) -> dict:
    stmt = select(Item).where(Item.location_id == location.id, Item.active.is_(True))
    if search:
        stmt = stmt.where(or_(Item.name.ilike(f"%{search}%"), Item.category.ilike(f"%{search}%")))
    if low_stock_only:
        stmt = stmt.where(
            Item.stock_qty.is_not(None),
            Item.low_stock_threshold.is_not(None),
            Item.stock_qty <= Item.low_stock_threshold,
        )
    items = list(await session.scalars(stmt.order_by(Item.name).limit(min(limit, MAX_ROWS))))
    tracked = await session.scalar(
        select(func.count(Item.id)).where(Item.location_id == location.id, Item.stock_qty.is_not(None))
    )
    return {
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "category": i.category,
                "stock_qty": i.stock_qty,
                "unit": i.unit,
                "low_stock_threshold": i.low_stock_threshold,
                "low": i.stock_qty is not None and i.low_stock_threshold is not None
                and i.stock_qty <= i.low_stock_threshold,
                "price": fmt_money(i.price_cents, location.currency),
                "price_cents": i.price_cents,
                "cost_cents": i.cost_cents,
            }
            for i in items
        ],
        "items_with_stock_tracking": tracked or 0,
    }


async def list_orders(
    session: AsyncSession, location: Location, period: str = "today", status: str | None = None,
    min_total_cents: int | None = None, limit: int = 20,
) -> dict:
    start, end = _range(location, period)
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    stmt = select(Order).where(Order.location_id == location.id, Order.opened_at >= s, Order.opened_at < e)
    if status:
        stmt = stmt.where(Order.status == status)
    if min_total_cents is not None:
        stmt = stmt.where(Order.total_cents >= min_total_cents)
    count = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    orders = list(await session.scalars(stmt.order_by(Order.opened_at.desc()).limit(min(limit, MAX_ROWS))))
    tz = ZoneInfo(location.timezone)
    return {
        "period": period,
        "total_matching": count or 0,
        "orders": [
            {
                "id": o.id,
                "opened": o.opened_at.astimezone(tz).strftime("%a %d %b %H:%M"),
                "status": o.status,
                "total": fmt_money(o.total_cents, location.currency),
                "total_cents": o.total_cents,
                "table": o.table_name,
                "employee": o.employee_name,
                "items": ", ".join(f"{ln.qty:g}x {ln.name}" for ln in o.lines[:8]),
            }
            for o in orders
        ],
    }


async def tables(session: AsyncSession, location: Location) -> dict:
    rows = list(
        await session.scalars(select(DiningTable).where(DiningTable.location_id == location.id).order_by(DiningTable.name))
    )
    counts: dict[str, int] = defaultdict(int)
    for t in rows:
        counts[t.status] += 1
    return {
        "summary": dict(counts),
        "tables": [{"id": t.id, "name": t.name, "seats": t.seats, "status": t.status} for t in rows],
    }


async def list_reviews(
    session: AsyncSession, location: Location, period: str = "last_30_days", max_rating: int | None = None,
    unreplied_only: bool = False, limit: int = 20,
) -> dict:
    start, end = _range(location, period)
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    stmt = select(Review).where(Review.location_id == location.id, Review.posted_at >= s, Review.posted_at < e)
    if max_rating:
        stmt = stmt.where(Review.rating <= max_rating)
    if unreplied_only:
        stmt = stmt.where(Review.reply_text.is_(None))
    reviews = list(await session.scalars(stmt.order_by(Review.posted_at.desc()).limit(min(limit, MAX_ROWS))))
    avg = await session.scalar(
        select(func.avg(Review.rating)).where(
            and_(Review.location_id == location.id, Review.posted_at >= s, Review.posted_at < e)
        )
    )
    return {
        "period": period,
        "average_rating": round(float(avg), 2) if avg is not None else None,
        "reviews": [
            {
                "id": r.id,
                "source": r.source,
                "rating": r.rating,
                "author": r.author,
                "posted": r.posted_at.date().isoformat(),
                "text": (r.text or "")[:600],
                "replied": r.reply_text is not None,
                "sentiment": r.sentiment,
                "topics": r.topics,
                "suggested_reply": r.suggested_reply,
            }
            for r in reviews
        ],
    }


async def dashboard(session: AsyncSession, location: Location, period: str = "today") -> dict:
    today = local_today(location)
    trend_rows = await _daily(session, location, today - timedelta(days=27), today)
    by_day = {r.day: r for r in trend_rows}
    trend = [
        {"day": (today - timedelta(days=i)).isoformat(),
         "gross_cents": by_day[today - timedelta(days=i)].gross_cents if (today - timedelta(days=i)) in by_day else 0}
        for i in range(27, -1, -1)
    ]
    return {
        "location": {"id": location.id, "name": location.name, "currency": location.currency,
                     "timezone": location.timezone},
        "as_of": datetime.now(ZoneInfo(location.timezone)).isoformat(timespec="minutes"),
        "summary": await sales_summary(session, location, period, "same_weekday_last_week"),
        "by_hour": await sales_breakdown(session, location, period, "hour"),
        "top_items": await top_items(session, location, period, limit=5),
        "low_stock": await inventory(session, location, low_stock_only=True, limit=10),
        "reviews": await list_reviews(session, location, "last_7_days", limit=5),
        "trend_28d": trend,
    }
