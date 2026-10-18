"""Low stock -> draft purchase order -> manager approves -> email to the supplier.

Nothing reaches a supplier without a manager's approval in the app.
"""

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Location, PurchaseOrder, Supplier, User, utcnow
from app.services.jobs import enqueue, handler


def order_qty(item: Item) -> float:
    """How much to order: the item's reorder quantity, else enough to reach twice the alert level."""
    if item.reorder_qty:
        return float(item.reorder_qty)
    threshold = item.low_stock_threshold or 0
    stock = max(0.0, item.stock_qty or 0)
    return float(max(1, math.ceil(threshold * 2 - stock)))


async def low_items(session: AsyncSession, location: Location) -> list[Item]:
    return list(await session.scalars(
        select(Item).where(
            Item.location_id == location.id, Item.active.is_(True), Item.stock_qty.is_not(None),
            Item.low_stock_threshold.is_not(None), Item.stock_qty <= Item.low_stock_threshold,
        ).order_by(Item.name)
    ))


async def draft_from_low_stock(session: AsyncSession, location: Location) -> dict:
    """Add every low item that has a supplier to that supplier's open draft (creating it if needed).
    Returns the drafts touched and the low items that have no supplier yet."""
    drafts: dict[str, PurchaseOrder] = {}
    unassigned: list[str] = []
    for item in await low_items(session, location):
        if not item.supplier_id:
            unassigned.append(item.name)
            continue
        po = drafts.get(item.supplier_id) or await session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.location_id == location.id,
                                        PurchaseOrder.supplier_id == item.supplier_id,
                                        PurchaseOrder.status == "draft")
        )
        if po is None:
            po = PurchaseOrder(location_id=location.id, supplier_id=item.supplier_id, status="draft", lines=[])
            session.add(po)
        if not any(line.get("item_id") == item.id for line in po.lines):
            po.lines = [*po.lines, {"item_id": item.id, "name": item.name, "qty": order_qty(item),
                                    "unit": item.unit}]
        drafts[item.supplier_id] = po
    await session.flush()
    return {"drafts": list(drafts.values()), "no_supplier": unassigned}


def render_order(location: Location, supplier: Supplier, po: PurchaseOrder, contact: User | None) -> tuple[str, str]:
    lines = [f"- {line['qty']:g} {line.get('unit') or ''} {line['name']}".replace("  ", " ") for line in po.lines]
    who = (contact.name or contact.email) if contact else location.name
    body = "\n".join([
        f"Hello {supplier.name},",
        "",
        f"Please send the following to {location.name}{' (' + location.address + ')' if location.address else ''}:",
        *lines,
        "",
        *( [f"Note: {po.note}", ""] if po.note else [] ),
        "Please reply to confirm the delivery date.",
        f"Thank you, {who}" + (f" ({contact.email})" if contact else ""),
        f"Order ref {po.id[:8]}",
    ])
    return f"Order from {location.name} (ref {po.id[:8]})", body


async def approve(session: AsyncSession, po: PurchaseOrder, user_id: str, note: str | None = None) -> PurchaseOrder:
    if po.status != "draft":
        raise ValueError(f"This order is already {po.status}")
    if not po.lines:
        raise ValueError("The order has no items")
    supplier = await session.get(Supplier, po.supplier_id) if po.supplier_id else None
    if supplier is None or not supplier.email:
        raise ValueError("Add the supplier's email address before approving")
    po.status, po.approved_by, po.approved_at = "approved", user_id, utcnow()
    if note is not None:
        po.note = note
    await enqueue(session, "send_purchase_order", {"purchase_order_id": po.id}, dedupe_key=f"po:{po.id}")
    await session.commit()
    return po


@handler("send_purchase_order")
async def handle_send_purchase_order(session: AsyncSession, payload: dict) -> None:
    from app.services.notify import Skipped, send_email

    po = await session.get(PurchaseOrder, payload["purchase_order_id"])
    if po is None or po.status != "approved":
        return
    supplier = await session.get(Supplier, po.supplier_id)
    location = await session.get(Location, po.location_id)
    contact = await session.get(User, po.approved_by) if po.approved_by else None
    subject, body = render_order(location, supplier, po, contact)
    try:
        await send_email(supplier.email, subject, body)
        po.status, po.sent_at, po.send_error = "sent", utcnow(), None
    except Skipped as exc:
        po.send_error = f"Not sent: {exc}. Send it yourself or set up email."
    await session.commit()


def po_out(po: PurchaseOrder, supplier_name: str | None) -> dict:
    return {"id": po.id, "supplier_id": po.supplier_id, "supplier": supplier_name, "status": po.status,
            "lines": po.lines, "note": po.note, "created_at": po.created_at.isoformat() if po.created_at else None,
            "approved_at": po.approved_at.isoformat() if po.approved_at else None,
            "sent_at": po.sent_at.isoformat() if po.sent_at else None, "send_error": po.send_error}


async def list_orders(session: AsyncSession, location: Location, status: str | None = None, limit: int = 30) -> list[dict]:
    stmt = select(PurchaseOrder, Supplier.name).outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
        PurchaseOrder.location_id == location.id)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    rows = await session.execute(stmt.order_by(PurchaseOrder.created_at.desc()).limit(limit))
    return [po_out(po, name) for po, name in rows]
