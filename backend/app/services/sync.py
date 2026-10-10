"""Bring POS data into our database and keep the roll-up tables current."""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pos.base import NormalizedItem, NormalizedOrder, POSRateLimited
from app.integrations.pos.registry import get_adapter
from app.models import (
    DailySales,
    Integration,
    Item,
    ItemSalesDaily,
    Location,
    Order,
    OrderLine,
    utcnow,
)
from app.services.integration_store import access_token
from app.services.jobs import RetryLater
from app.services.rollup_math import OrderRow, compute_rollups

log = logging.getLogger(__name__)

INITIAL_BACKFILL_DAYS = 90  # Clover limits some order filters to the last 90 days
SYNC_OVERLAP = timedelta(minutes=10)  # re-read a little history so clock skew never drops an order


# --- Upserts --------------------------------------------------------------------------------------


async def upsert_items(session: AsyncSession, location: Location, provider: str, items: list[NormalizedItem]):
    existing = {
        i.external_id: i
        for i in await session.scalars(
            select(Item).where(Item.location_id == location.id, Item.provider == provider)
        )
    }
    for n in items:
        row = existing.get(n.external_id)
        if row is None:
            row = Item(location_id=location.id, provider=provider, external_id=n.external_id)
            session.add(row)
        row.name = n.name
        row.category = n.category
        row.price_cents = n.price_cents
        row.cost_cents = n.cost_cents
        row.stock_qty = n.stock_qty
        row.unit = n.unit
        row.active = n.active
    await session.flush()


async def upsert_orders(
    session: AsyncSession, location: Location, provider: str, orders: list[NormalizedOrder]
) -> set[date]:
    """Insert or update orders; returns the local days whose roll-ups must be rebuilt."""
    if not orders:
        return set()
    tz = ZoneInfo(location.timezone)
    categories = {
        i.external_id: i.category
        for i in await session.scalars(
            select(Item).where(Item.location_id == location.id, Item.provider == provider)
        )
    }
    ids = [o.external_id for o in orders]
    existing = {
        o.external_id: o
        for o in await session.scalars(
            select(Order).where(
                Order.location_id == location.id, Order.provider == provider, Order.external_id.in_(ids)
            )
        )
    }
    days: set[date] = set()
    for n in orders:
        row = existing.get(n.external_id)
        if row is None:
            row = Order(location_id=location.id, provider=provider, external_id=n.external_id)
            session.add(row)
        else:
            days.add(row.opened_at.astimezone(tz).date())  # old day changes too if the order moved
        row.status = n.status
        row.total_cents = n.total_cents
        row.tax_cents = n.tax_cents
        row.tip_cents = n.tip_cents
        row.table_name = n.table_name
        row.employee_name = n.employee_name
        row.guests = n.guests
        row.opened_at = n.opened_at
        row.closed_at = n.closed_at
        row.lines = [
            OrderLine(
                item_external_id=ln.item_external_id,
                name=ln.name,
                category=ln.category or categories.get(ln.item_external_id or ""),
                qty=ln.qty,
                unit_price_cents=ln.unit_price_cents,
                total_cents=ln.total_cents,
            )
            for ln in n.lines
        ]
        days.add(n.opened_at.astimezone(tz).date())
    await session.flush()
    return days


# --- Roll-ups -------------------------------------------------------------------------------------


def _day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


async def rebuild_rollups(session: AsyncSession, location: Location, days: set[date]) -> None:
    for day in sorted(days):
        start, end = _day_bounds(day, location.timezone)
        orders = list(
            await session.scalars(
                select(Order).where(
                    Order.location_id == location.id, Order.opened_at >= start, Order.opened_at < end
                )
            )
        )
        rows = [
            OrderRow(
                status=o.status,
                opened_at=o.opened_at,
                total_cents=o.total_cents,
                tax_cents=o.tax_cents,
                tip_cents=o.tip_cents,
                guests=o.guests,
                lines=[(ln.name, ln.category, ln.qty, ln.total_cents) for ln in o.lines],
            )
            for o in orders
        ]
        day_totals, item_totals = compute_rollups(rows, location.timezone)
        await session.execute(
            delete(DailySales).where(DailySales.location_id == location.id, DailySales.day == day)
        )
        await session.execute(
            delete(ItemSalesDaily).where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day == day)
        )
        t = day_totals.get(day)
        if t:
            session.add(
                DailySales(
                    location_id=location.id,
                    day=day,
                    gross_cents=t.gross_cents,
                    tax_cents=t.tax_cents,
                    tip_cents=t.tip_cents,
                    orders_count=t.orders_count,
                    guests=t.guests,
                    by_hour=t.by_hour,
                )
            )
        for (d, name), (category, qty, cents) in item_totals.items():
            if d == day:
                session.add(
                    ItemSalesDaily(
                        location_id=location.id, day=d, name=name, category=category, qty=qty, revenue_cents=cents
                    )
                )
    await session.flush()


# --- Full / incremental sync ----------------------------------------------------------------------


async def sync_pos(session: AsyncSession, integ: Integration, *, full: bool = False) -> dict:
    location = await session.get(Location, integ.location_id)
    adapter = get_adapter(integ.provider)
    token = await access_token(session, integ)
    started = utcnow()
    cursor = dict(integ.cursor or {})
    since_raw = None if full else cursor.get("orders_modified_since")
    since = (
        datetime.fromisoformat(since_raw) - SYNC_OVERLAP
        if since_raw
        else started - timedelta(days=INITIAL_BACKFILL_DAYS)
    )
    try:
        # Items first so order lines can pick up categories.
        if full or not cursor.get("items_synced_at") or _older_than(cursor.get("items_synced_at"), hours=6):
            items = await adapter.fetch_items(token, integ.external_id)
            await upsert_items(session, location, integ.provider, items)
            cursor["items_synced_at"] = started.isoformat()
        orders = await adapter.fetch_orders(token, integ.external_id, since)
    except POSRateLimited as exc:
        raise RetryLater(exc.retry_after) from exc
    days = await upsert_orders(session, location, integ.provider, orders)
    await rebuild_rollups(session, location, days)
    cursor["orders_modified_since"] = started.isoformat()
    integ.cursor = cursor
    integ.last_synced_at = started
    integ.status = "active"
    integ.last_error = None
    await session.commit()
    from app.services.alerts import check_low_stock

    await check_low_stock(session, location)
    return {"orders": len(orders), "days_rebuilt": len(days)}


def _older_than(iso: str, *, hours: int) -> bool:
    return datetime.fromisoformat(iso) < utcnow() - timedelta(hours=hours)


# --- Webhooks -------------------------------------------------------------------------------------


async def apply_webhook(session: AsyncSession, provider: str, body: dict) -> dict:
    """Process one POS webhook delivery (called from a job, not the request, so Clover gets a fast 200)."""
    adapter = get_adapter(provider)
    changes = adapter.parse_webhook(body)
    handled = 0
    by_merchant: dict[str, list] = {}
    for c in changes:
        by_merchant.setdefault(c.merchant_id, []).append(c)
    for merchant_id, merchant_changes in by_merchant.items():
        integ = await session.scalar(
            select(Integration).where(
                Integration.provider == provider,
                Integration.external_id == merchant_id,
                Integration.status != "disconnected",
            )
        )
        if integ is None:
            log.info("webhook for unknown merchant %s ignored", merchant_id)
            continue
        location = await session.get(Location, integ.location_id)
        token = await access_token(session, integ)
        days: set[date] = set()
        try:
            for c in merchant_changes:
                if c.object_type == "order":
                    if c.change == "DELETE":
                        row = await session.scalar(
                            select(Order).where(
                                Order.location_id == location.id,
                                Order.provider == provider,
                                Order.external_id == c.object_id,
                            )
                        )
                        if row:
                            row.status = "void"
                            days.add(row.opened_at.astimezone(ZoneInfo(location.timezone)).date())
                    else:
                        order = await adapter.fetch_order(token, merchant_id, c.object_id)
                        if order:
                            days |= await upsert_orders(session, location, provider, [order])
                    handled += 1
                elif c.object_type == "item" and hasattr(adapter, "fetch_item"):
                    if c.change == "DELETE":
                        row = await session.scalar(
                            select(Item).where(Item.location_id == location.id, Item.external_id == c.object_id)
                        )
                        if row:
                            row.active = False
                    else:
                        item = await adapter.fetch_item(token, merchant_id, c.object_id)
                        if item:
                            await upsert_items(session, location, provider, [item])
                    handled += 1
        except POSRateLimited as exc:
            raise RetryLater(exc.retry_after) from exc
        await rebuild_rollups(session, location, days)
        await session.commit()
        from app.services.alerts import check_low_stock

        await check_low_stock(session, location)
    return {"changes": len(changes), "handled": handled}
