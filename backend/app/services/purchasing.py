"""Low stock -> draft purchase order -> manager approves -> message to the supplier (email, text, or sent by hand).

Nothing reaches a supplier without a manager's approval in the app.
"""

import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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


# --- Messages to suppliers ------------------------------------------------------------------------------------

DEFAULT_TEMPLATE = """Hello {supplier},

I want to order:
{items}

for {restaurant} on {delivery_date}.
Delivery address: {address}
{note}

Thank you,
{your_name}
{phone}"""

PLACEHOLDERS = [
    ("supplier", "Supplier's name"), ("items", "Items and quantities"), ("restaurant", "Your restaurant"),
    ("delivery_date", "Delivery date"), ("address", "Your address"), ("phone", "Your phone"),
    ("your_name", "Your name"), ("note", "Note on this order"), ("ref", "Order number"),
]
PLACEHOLDER = re.compile(r"\{(\w+)\}")
MAX_MESSAGE = 4000
CHANNELS = ("email", "sms", "manual")


def template_for(location: Location) -> str:
    return location.order_template or DEFAULT_TEMPLATE


def fill(template: str, values: dict[str, str]) -> str:
    """Put the order's details into the template. A line whose fill-ins are all empty (say, no note) is dropped;
    unknown {words} are left as typed."""
    out = []
    for line in template.replace("\r", "").split("\n"):
        keys = [k for k in PLACEHOLDER.findall(line) if k in values]
        filled = PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), line)
        if keys and not any(values[k] for k in keys):
            continue  # e.g. "Delivery address: {address}" when there's no address
        out.append(filled)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def items_text(lines: list[dict]) -> str:
    return "\n".join(f"- {float(ln['qty']):g} {ln.get('unit') or ''} {ln['name']}".replace("  ", " ") for ln in lines)


def nice_date(d: date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}"


def default_delivery_date(location: Location) -> date:
    try:
        tz = ZoneInfo(location.timezone or "America/Vancouver")
    except Exception:  # noqa: BLE001 - unknown zone name
        tz = ZoneInfo("America/Vancouver")
    return datetime.now(tz).date() + timedelta(days=1)


def order_values(location: Location, supplier: Supplier | None, po: PurchaseOrder, sender: User | None,
                 delivery: date) -> dict[str, str]:
    return {
        "supplier": supplier.name if supplier else "", "items": items_text(po.lines), "restaurant": location.name,
        "delivery_date": nice_date(delivery), "address": location.address or "", "phone": location.phone or "",
        "your_name": (sender.name or "") if sender else "", "note": po.note or "", "ref": po.id[:8],
    }


def channels_ready() -> dict[str, bool]:
    s = get_settings()
    return {"email": bool(s.resend_api_key), "sms": bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_sms_from)}


def compose(location: Location, supplier: Supplier | None, po: PurchaseOrder, sender: User | None,
            delivery: date | None = None) -> dict:
    """The message for this order, from the restaurant's template (or the text already sent)."""
    delivery = delivery or po.delivery_date or default_delivery_date(location)
    # A draft saved with its own wording keeps it (editing the draft's items clears it again).
    body = po.message if po.message else fill(
        template_for(location), order_values(location, supplier, po, sender, delivery))
    return {"subject": f"Order from {location.name} for {nice_date(delivery)}", "body": body,
            "delivery_date": delivery.isoformat(), "to_email": supplier.email if supplier else None,
            "to_phone": supplier.phone if supplier else None, "ready": channels_ready()}


async def send(session: AsyncSession, location: Location, po: PurchaseOrder, sender: User | None, channel: str,
               message: str | None = None, delivery: date | None = None, note: str | None = None) -> PurchaseOrder:
    """Approve the order and send it: by email or text in the background, or 'manual' when the manager sent
    it themselves (WhatsApp, their own email, a phone call)."""
    if channel not in CHANNELS:
        raise ValueError("Choose email, text, or sent yourself")
    if po.status not in ("draft", "approved"):
        raise ValueError(f"This order is already {po.status}")
    if po.status == "approved" and not po.send_error:
        raise ValueError("This order is already being sent")
    if not po.lines:
        raise ValueError("The order has no items")
    supplier = await session.get(Supplier, po.supplier_id) if po.supplier_id else None
    if channel == "email" and not (supplier and supplier.email):
        raise ValueError("Add the supplier's email address first, or send it yourself")
    if channel == "sms" and not (supplier and supplier.phone):
        raise ValueError("Add the supplier's phone number first, or send it yourself")
    if channel == "sms" and not channels_ready()["sms"]:
        raise ValueError("Texting isn't set up yet: send it yourself, or by email")
    if note is not None:
        po.note = note
    if delivery is not None:
        po.delivery_date = delivery
    po.delivery_date = po.delivery_date or default_delivery_date(location)
    text = (message or "").strip()[:MAX_MESSAGE] or fill(
        template_for(location), order_values(location, supplier, po, sender, po.delivery_date))
    po.message, po.sent_via, po.send_error = text, channel, None
    if po.status == "draft":
        po.status, po.approved_by, po.approved_at = "approved", sender.id if sender else None, utcnow()
    if channel == "manual":
        po.status, po.sent_at = "sent", utcnow()
    else:
        await enqueue(session, "send_purchase_order", {"purchase_order_id": po.id},
                      dedupe_key=f"po:{po.id}:{channel}:{int(utcnow().timestamp())}")
    await session.commit()
    return po


async def approve(session: AsyncSession, po: PurchaseOrder, user_id: str, note: str | None = None) -> PurchaseOrder:
    """Approve and email (the original one-click path, also used by the assistant)."""
    if po.status != "draft":
        raise ValueError(f"This order is already {po.status}")
    location = await session.get(Location, po.location_id)
    return await send(session, location, po, await session.get(User, user_id), "email", note=note)


@handler("send_purchase_order")
async def handle_send_purchase_order(session: AsyncSession, payload: dict) -> None:
    from app.services.notify import Skipped, send_email, send_twilio

    po = await session.get(PurchaseOrder, payload["purchase_order_id"])
    if po is None or po.status != "approved":
        return
    supplier = await session.get(Supplier, po.supplier_id) if po.supplier_id else None
    location = await session.get(Location, po.location_id)
    sender = await session.get(User, po.approved_by) if po.approved_by else None
    msg = compose(location, supplier, po, sender)
    try:
        if supplier is None:
            raise Skipped("the supplier was deleted")
        if po.sent_via == "sms":
            await send_twilio(supplier.phone or "", msg["body"], whatsapp=False)
        else:
            if not supplier.email:
                raise Skipped("the supplier has no email address")
            await send_email(supplier.email, msg["subject"], msg["body"])
        po.status, po.sent_at, po.send_error = "sent", utcnow(), None
    except Skipped as exc:
        how = "text" if po.sent_via == "sms" else "email"
        po.send_error = f"Not sent by {how}: {exc}. Send it yourself, then mark it as sent."
    await session.commit()


def po_out(po: PurchaseOrder, supplier_name: str | None) -> dict:
    return {"id": po.id, "supplier_id": po.supplier_id, "supplier": supplier_name, "status": po.status,
            "lines": po.lines, "note": po.note, "created_at": po.created_at.isoformat() if po.created_at else None,
            "approved_at": po.approved_at.isoformat() if po.approved_at else None,
            "sent_at": po.sent_at.isoformat() if po.sent_at else None, "send_error": po.send_error,
            "delivery_date": po.delivery_date.isoformat() if po.delivery_date else None, "message": po.message,
            "sent_via": po.sent_via}


async def list_orders(session: AsyncSession, location: Location, status: str | None = None, limit: int = 30) -> list[dict]:
    stmt = select(PurchaseOrder, Supplier.name).outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
        PurchaseOrder.location_id == location.id)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    rows = await session.execute(stmt.order_by(PurchaseOrder.created_at.desc()).limit(limit))
    return [po_out(po, name) for po, name in rows]
