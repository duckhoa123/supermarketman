"""Remove the demo restaurant's sample menu and sales once real POS data is coming in.

Demo rows are the ones the seed marks with provider "demo". Anything synced from Clover or Square, and
everything the owner set up (settings, website, tables, staff), stays.
"""

from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySales, Item, ItemSalesDaily, Location, Order, OrderLine, PurchaseOrder
from app.services.sync import rebuild_rollups

DEMO = "demo"


async def counts(session: AsyncSession, location: Location) -> dict:
    items = await session.scalar(select(func.count(Item.id)).where(Item.location_id == location.id,
                                                                   Item.provider == DEMO))
    orders = await session.scalar(select(func.count(Order.id)).where(Order.location_id == location.id,
                                                                     Order.provider == DEMO))
    return {"items": items or 0, "orders": orders or 0}


async def clear(session: AsyncSession, location: Location) -> dict:
    """Delete demo items, demo orders (and the sales totals built from them) and supplier drafts for demo items."""
    found = await counts(session, location)
    demo_ids = set(await session.scalars(select(Item.id).where(Item.location_id == location.id, Item.provider == DEMO)))
    drafts = 0
    for po in await session.scalars(select(PurchaseOrder).where(PurchaseOrder.location_id == location.id,
                                                                 PurchaseOrder.status.in_(("draft", "approved")))):
        ids = {ln.get("item_id") for ln in po.lines or [] if isinstance(ln, dict)}
        if ids and ids <= demo_ids:
            await session.delete(po)
            drafts += 1
    demo_orders = select(Order.id).where(Order.location_id == location.id, Order.provider == DEMO)
    await session.execute(delete(OrderLine).where(OrderLine.order_id.in_(demo_orders)))
    await session.execute(delete(Order).where(Order.location_id == location.id, Order.provider == DEMO))
    await session.execute(delete(Item).where(Item.location_id == location.id, Item.provider == DEMO))
    # Sales totals are rebuilt from the orders that remain (the real ones).
    await session.execute(delete(DailySales).where(DailySales.location_id == location.id))
    await session.execute(delete(ItemSalesDaily).where(ItemSalesDaily.location_id == location.id))
    zone = ZoneInfo(location.timezone)
    days: set[date] = {o.astimezone(zone).date() for o in await session.scalars(
        select(Order.opened_at).where(Order.location_id == location.id))}
    await session.flush()
    await rebuild_rollups(session, location, days)
    await session.commit()
    return {**found, "supplier_drafts": drafts}
