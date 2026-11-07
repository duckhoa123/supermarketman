"""Changes made in Pass that go to the restaurant's POS: new and edited menu items, and orders.

The POS stays the source of truth. Each change is sent to the POS first and only then saved in Pass, so the two
never disagree; the regular sync later brings back anything the POS adds (taxes, payments, edits at the device).
Without a connected POS, items are kept in Pass only and orders can't be sent.
"""

import uuid
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pos.base import OutLine, POSWriteError
from app.integrations.pos.registry import get_adapter
from app.models import CustomerOrder, Integration, Item, Location, Order, OrderLine, utcnow
from app.services.integration_store import access_token, pos_integration
from app.services.sync import rebuild_rollups

LOCAL = "pass"  # items made in Pass while no POS is connected


class PosError(ValueError):
    pass


async def _writable(session: AsyncSession, location: Location) -> Integration | None:
    integ = await pos_integration(session, location.id)
    if integ is None or integ.status != "active" or not integ.external_id:
        return None
    adapter = get_adapter(integ.provider)
    return integ if hasattr(adapter, "create_item") else None


async def status(session: AsyncSession, location: Location) -> dict:
    integ = await _writable(session, location)
    return {"pos": integ.provider if integ else None}


async def add_item(session: AsyncSession, location: Location, *, name: str, price_cents: int,
                   category: str | None, stock_qty: float | None) -> Item:
    integ = await _writable(session, location)
    if integ is not None:
        adapter = get_adapter(integ.provider)
        token = await access_token(session, integ)
        try:
            made = await adapter.create_item(token, integ.external_id, name=name, price_cents=price_cents,
                                             category=category, stock_qty=stock_qty)
        except POSWriteError as exc:
            raise PosError(str(exc)) from exc
        provider, external_id = integ.provider, made.external_id
    else:
        provider, external_id = LOCAL, f"pass-{uuid.uuid4().hex[:12]}"
    item = Item(location_id=location.id, provider=provider, external_id=external_id, name=name, category=category,
                price_cents=price_cents, stock_qty=stock_qty, active=True)
    session.add(item)
    await session.commit()
    return item


async def edit_item(session: AsyncSession, location: Location, item: Item, changes: dict) -> Item:
    """Name, price, category and stock. Items from the POS are changed there first."""
    new = {"name": changes.get("name", item.name), "price_cents": changes.get("price_cents", item.price_cents),
           "category": changes.get("category", item.category), "stock_qty": changes.get("stock_qty", item.stock_qty)}
    if item.provider not in (LOCAL, "demo"):
        integ = await _writable(session, location)
        if integ is None or integ.provider != item.provider:
            raise PosError(f"This item comes from {item.provider.title()}, which isn't connected. "
                           "Connect it again in Settings to change the item.")
        adapter = get_adapter(integ.provider)
        token = await access_token(session, integ)
        try:
            await adapter.update_item(token, integ.external_id, item.external_id, name=new["name"],
                                      price_cents=new["price_cents"], category=new["category"],
                                      old_category=item.category, stock_qty=new["stock_qty"],
                                      stock_changed="stock_qty" in changes and changes["stock_qty"] != item.stock_qty)
        except POSWriteError as exc:
            raise PosError(str(exc)) from exc
    for k, v in new.items():
        setattr(item, k, v)
    await session.commit()
    return item


async def _out_lines(session: AsyncSession, location: Location, provider: str, lines: list[dict]) -> list[OutLine]:
    """lines: [{"item_id" (Pass id) or "name"+"unit_price_cents", "qty"}]."""
    ids = [ln["item_id"] for ln in lines if ln.get("item_id")]
    items = {i.id: i for i in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                                      Item.id.in_(ids)))} if ids else {}
    out = []
    for ln in lines:
        qty = max(1, int(round(float(ln.get("qty") or 1))))
        it = items.get(ln.get("item_id"))
        if ln.get("item_id") and it is None:
            raise PosError("One of the items isn't on this restaurant's menu")
        if it is not None:
            out.append(OutLine(name=it.name, unit_price_cents=it.price_cents, qty=qty,
                               item_external_id=it.external_id if it.provider == provider else None))
        else:
            out.append(OutLine(name=str(ln.get("name") or "Item")[:127],
                               unit_price_cents=int(ln.get("unit_price_cents") or 0), qty=qty))
    return out


async def send_order(session: AsyncSession, location: Location, lines: list[dict], *, title: str,
                     note: str | None = None) -> Order:
    """Create an open order in the POS and show it in Pass straight away."""
    integ = await _writable(session, location)
    if integ is None:
        raise PosError("Connect Clover in Settings first: orders made here are sent to your POS.")
    out = await _out_lines(session, location, integ.provider, lines)
    adapter = get_adapter(integ.provider)
    token = await access_token(session, integ)
    try:
        external_id = await adapter.create_order(token, integ.external_id, out, title=title, note=note)
    except POSWriteError as exc:
        raise PosError(str(exc)) from exc
    categories = {i.external_id: i.category for i in await session.scalars(
        select(Item).where(Item.location_id == location.id, Item.provider == integ.provider))}
    now = utcnow()
    order = Order(location_id=location.id, provider=integ.provider, external_id=external_id, status="open",
                  total_cents=sum(ln.unit_price_cents * ln.qty for ln in out), opened_at=now)
    order.lines = [OrderLine(item_external_id=ln.item_external_id, name=ln.name,
                             category=categories.get(ln.item_external_id), qty=ln.qty,
                             unit_price_cents=ln.unit_price_cents, total_cents=ln.unit_price_cents * ln.qty)
                   for ln in out]
    session.add(order)
    await session.flush()
    today: date = now.astimezone(ZoneInfo(location.timezone)).date()
    await rebuild_rollups(session, location, {today})
    await session.commit()
    return order


async def push_customer_order(session: AsyncSession, order: CustomerOrder) -> dict:
    """An accepted customer-app order goes to the POS so the kitchen sees it with everything else."""
    if order.pos_order_id:
        return {"ok": True, "pos_order_id": order.pos_order_id}
    location = await session.get(Location, order.location_id)
    if await _writable(session, location) is None:
        return {"ok": False, "skipped": "no POS"}
    paid = order.payment_status in ("paid", "test")
    kind = "Delivery" if order.fulfilment == "delivery" else "Pickup"
    note = " · ".join(filter(None, [
        "PAID in the Pass app: don't charge again" if paid else "Not paid yet: take payment at pickup",
        f"Customer note: {order.notes}" if order.notes else None]))
    lines = [{"item_id": ln.get("item_id"), "name": ln.get("name"), "qty": ln.get("qty") or 1,
              "unit_price_cents": ln.get("unit_price_cents") or 0} for ln in order.lines]
    try:
        sent = await send_order(session, location, lines, title=f"Pass app {kind.lower()} #{order.id[:6]}", note=note)
    except PosError as exc:
        order.pos_error = str(exc)[:500]
        from app.services.alerts import create_alert

        await create_alert(session, location, kind="pos_push_failed", title="Couldn't send an app order to Clover",
                           body=f"{kind} order #{order.id[:6]}: {exc}", data={"customer_order_id": order.id},
                           dedupe_key=f"pos_push:{order.id}", channels=["push"])
        await session.commit()
        return {"ok": False, "error": str(exc)}
    order.pos_order_id, order.pos_error = sent.external_id, None
    await session.commit()
    return {"ok": True, "pos_order_id": sent.external_id}
