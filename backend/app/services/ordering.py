"""Customer orders: quote, place, move through the kitchen and delivery, tip.

One order touches all three agents: the customer places it, the restaurant accepts and prepares it,
and for delivery the fleet assigns one of our drivers when it is ready.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CustomerOrder, Delivery, Item, Location, NutritionEntry, User, utcnow
from app.money import fmt_money
from app.services import geo, payments
from app.services.alerts import create_alert, create_user_alert

PREP_MINUTES = 20  # until restaurants set their own, the time from accepting to ready
MAX_LINES = 30

# Who may move an order from one status to the next.
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "restaurant": {
        "placed": {"accepted", "cancelled"},
        "accepted": {"ready", "cancelled"},
        "ready": {"picked_up"},  # pickup orders collected at the counter
    },
    "driver": {"ready": {"out_for_delivery"}, "out_for_delivery": {"delivered"}},
    "customer": {"placed": {"cancelled"}},
}


class OrderError(ValueError):
    pass


def can_move(actor: str, fulfilment: str, current: str, new: str) -> bool:
    if new not in TRANSITIONS.get(actor, {}).get(current, set()):
        return False
    if fulfilment == "delivery" and new == "picked_up":
        return False  # delivery orders leave with a driver
    if fulfilment == "pickup" and new == "out_for_delivery":
        return False
    return True


@dataclass
class Quote:
    lines: list[dict]
    subtotal_cents: int
    tax_cents: int
    delivery_fee_cents: int
    total_cents: int
    restaurant_fee_cents: int
    distance_km: float | None
    eta_minutes: int
    nutrition: dict

    def out(self, currency: str) -> dict:
        return {
            "lines": self.lines,
            "subtotal": fmt_money(self.subtotal_cents, currency), "tax": fmt_money(self.tax_cents, currency),
            "delivery_fee": fmt_money(self.delivery_fee_cents, currency),
            "total": fmt_money(self.total_cents, currency), "total_cents": self.total_cents,
            "distance_km": self.distance_km, "eta_minutes": self.eta_minutes, "nutrition": self.nutrition,
        }


async def quote(session: AsyncSession, location: Location, items: list[dict], fulfilment: str,
                dropoff: tuple[float, float] | None = None) -> Quote:
    """Price an order. `items` is [{"item_id": ..., "qty": n}]; prices always come from the menu, never the client."""
    s = get_settings()
    if fulfilment not in ("pickup", "delivery"):
        raise OrderError("Choose pickup or delivery")
    if not location.listed or not location.accepts_orders:
        raise OrderError(f"{location.name} isn't taking orders in the app yet")
    if not items or len(items) > MAX_LINES:
        raise OrderError("Add between 1 and 30 items")
    ids = [str(i.get("item_id")) for i in items]
    menu = {i.id: i for i in await session.scalars(select(Item).where(
        Item.location_id == location.id, Item.id.in_(ids), Item.active.is_(True), Item.orderable.is_(True)))}
    lines, subtotal = [], 0
    nutrition = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "complete": True}
    for req in items:
        item = menu.get(str(req.get("item_id")))
        if item is None:
            raise OrderError("An item in this order isn't on the menu any more")
        qty = int(req.get("qty") or 1)
        if not 1 <= qty <= 20:
            raise OrderError("Quantity must be between 1 and 20")
        if item.stock_qty is not None and item.stock_qty < qty:
            raise OrderError(f"{item.name} is sold out")
        total = item.price_cents * qty
        subtotal += total
        line = {"item_id": item.id, "name": item.name, "qty": qty, "unit_price_cents": item.price_cents,
                "total_cents": total}
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            v = getattr(item, key)
            line[key] = v
            if v is None:
                nutrition["complete"] = False
            else:
                nutrition[key] += v * qty
        lines.append(line)
    tax = round(subtotal * location.tax_rate_bp / 10_000)
    fee, km = 0, None
    if fulfilment == "delivery":
        if not location.accepts_delivery:
            raise OrderError(f"{location.name} offers pickup only")
        if dropoff is None:
            raise OrderError("Add a delivery address")
        if location.lat is None or location.lng is None:
            raise OrderError("This restaurant's address isn't set up for delivery yet")
        for lat, lng in ((location.lat, location.lng), dropoff):
            if not geo.within_km(lat, lng, s.delivery_hub_lat, s.delivery_hub_lng, s.delivery_radius_km):
                raise OrderError(f"Delivery is only available within {s.delivery_radius_km:g} km of our hub")
        km = geo.road_km(location.lat, location.lng, *dropoff)
        fee = geo.delivery_fee_cents(km, s.delivery_base_fee_cents, s.delivery_base_km, s.delivery_per_km_cents)
    fee_bp = s.restaurant_fee_delivery_bp if fulfilment == "delivery" else s.restaurant_fee_pickup_bp
    eta = PREP_MINUTES + (geo.HANDOFF_MIN * 2 + geo.travel_minutes(km) if km is not None else 0)
    return Quote(lines=lines, subtotal_cents=subtotal, tax_cents=tax, delivery_fee_cents=fee,
                 total_cents=subtotal + tax + fee, restaurant_fee_cents=geo.restaurant_fee_cents(subtotal, fee_bp),
                 distance_km=km, eta_minutes=eta, nutrition={k: round(v, 1) if isinstance(v, float) else v
                                                             for k, v in nutrition.items()})


async def place(session: AsyncSession, location: Location, user: User, *, items: list[dict], fulfilment: str,
                dropoff: tuple[float, float] | None = None, address: str | None = None, notes: str | None = None,
                share_contact: bool = False, tip_cents: int = 0) -> tuple[CustomerOrder, dict]:
    q = await quote(session, location, items, fulfilment, dropoff)
    tip = max(0, min(int(tip_cents or 0), 50_000))
    now = utcnow()
    order = CustomerOrder(
        location_id=location.id, customer_user_id=user.id, fulfilment=fulfilment, status="placed", lines=q.lines,
        subtotal_cents=q.subtotal_cents, tax_cents=q.tax_cents, delivery_fee_cents=q.delivery_fee_cents,
        tip_cents=tip, total_cents=q.total_cents + tip, restaurant_fee_cents=q.restaurant_fee_cents,
        delivery_address=address, dropoff_lat=dropoff[0] if dropoff else None,
        dropoff_lng=dropoff[1] if dropoff else None, notes=(notes or "")[:500] or None,
        share_contact=share_contact, placed_at=now, eta_at=now + timedelta(minutes=q.eta_minutes),
    )
    session.add(order)
    await session.flush()
    if fulfilment == "delivery":
        session.add(Delivery(customer_order_id=order.id, location_id=location.id, status="waiting",
                             pickup_lat=location.lat, pickup_lng=location.lng, dropoff_lat=dropoff[0],
                             dropoff_lng=dropoff[1], distance_km=q.distance_km, promised_at=order.eta_at,
                             eta_at=order.eta_at, tip_cents=tip))
    payment = await payments.charge(order, user)
    order.payment_status, order.payment_ref = payment["status"], payment.get("ref")
    await create_alert(
        session, location, kind="new_order",
        title=f"New {fulfilment} order: {fmt_money(q.subtotal_cents, location.currency)}",
        body=", ".join(f"{ln['qty']}x {ln['name']}" for ln in q.lines)[:300]
        + (f". Note: {order.notes}" if order.notes else ""),
        data={"customer_order_id": order.id}, dedupe_key=f"order:{order.id}", channels=["push"])
    await session.commit()
    return order, payment


def order_out(o: CustomerOrder, currency: str, *, restaurant: str | None = None, delivery: Delivery | None = None,
              for_restaurant: bool = False) -> dict:
    out = {
        "id": o.id, "location_id": o.location_id, "fulfilment": o.fulfilment, "status": o.status,
        "lines": [{k: ln.get(k) for k in ("item_id", "name", "qty", "total_cents")} for ln in o.lines],
        "subtotal": fmt_money(o.subtotal_cents, currency), "tax": fmt_money(o.tax_cents, currency),
        "delivery_fee": fmt_money(o.delivery_fee_cents, currency), "tip": fmt_money(o.tip_cents, currency),
        "total": fmt_money(o.total_cents, currency), "total_cents": o.total_cents,
        "payment_status": o.payment_status, "placed_at": o.placed_at.isoformat(),
        "eta_at": o.eta_at.isoformat() if o.eta_at else None, "notes": o.notes,
    }
    if restaurant:
        out["restaurant"] = restaurant
    if for_restaurant:
        out["platform_fee"] = fmt_money(o.restaurant_fee_cents, currency)
        out["delivery_address"] = o.delivery_address if o.fulfilment == "delivery" else None
    else:
        out["delivery_address"] = o.delivery_address
    if delivery is not None:
        out["delivery"] = {"status": delivery.status, "driver_assigned": delivery.driver_id is not None,
                           "eta_at": delivery.eta_at.isoformat() if delivery.eta_at else None}
    return out


CUSTOMER_MESSAGES = {
    "accepted": ("Order accepted", "{r} is preparing your order."),
    "ready": ("Ready for pickup", "Your order from {r} is ready to collect."),
    "out_for_delivery": ("On the way", "Your order from {r} has left the restaurant{eta}."),
    "delivered": ("Delivered", "Enjoy your meal from {r}! You can add a tip for your driver in the app; 100% goes to them."),
    "picked_up": ("Picked up", "Enjoy your meal from {r}!"),
    "cancelled": ("Order cancelled", "Your order from {r} was cancelled. Any payment will be refunded."),
}


async def set_status(session: AsyncSession, order: CustomerOrder, new: str, actor: str) -> CustomerOrder:
    """Move an order forward and tell the people who need to know."""
    from app.services import fleet, guests

    if not can_move(actor, order.fulfilment, order.status, new):
        raise OrderError(f"Can't change a {order.fulfilment} order from {order.status} to {new}")
    location = await session.get(Location, order.location_id)
    now = utcnow()
    order.status = new
    delivery = await session.scalar(select(Delivery).where(Delivery.customer_order_id == order.id))
    if new == "accepted":
        order.eta_at = max(order.eta_at or now, now + timedelta(minutes=PREP_MINUTES))
    elif new == "ready":
        order.ready_at = now
    elif new in ("delivered", "picked_up", "cancelled"):
        order.completed_at = now
    if new == "cancelled":
        if delivery is not None:
            delivery.status = "cancelled"
        if order.payment_status in ("paid", "test"):
            order.payment_status = "refunded"
    if new in ("delivered", "picked_up"):
        await log_order_nutrition(session, order, location)
        if order.share_contact:
            user = await session.get(User, order.customer_user_id)
            guest = await guests.guest_for_customer(session, location, user)
            await guests.record_visit(session, guest, order.subtotal_cents, now)
    title, body = CUSTOMER_MESSAGES.get(new, ("Order update", "Your order is now " + new))
    eta = ""
    if new == "out_for_delivery" and order.eta_at:
        from zoneinfo import ZoneInfo
        eta = ", arriving about " + order.eta_at.astimezone(ZoneInfo(location.timezone)).strftime("%H:%M")
    await create_user_alert(session, order.customer_user_id, kind="order_update", title=title,
                            body=body.format(r=location.name, eta=eta), data={"customer_order_id": order.id},
                            dedupe_key=f"order:{order.id}:{new}")
    if new == "cancelled" and actor == "customer":
        await create_alert(session, location, kind="new_order", title="Order cancelled by the customer",
                           body=f"Order {order.id[:8]} was cancelled.", data={"customer_order_id": order.id},
                           dedupe_key=f"order:{order.id}:cancelled", channels=["push"])
    await session.flush()
    if new == "ready" and delivery is not None:
        if await fleet.assign(session, delivery) is None:
            await fleet.check_busy(session)  # nobody free: call in on-call drivers
    await session.commit()
    return order


async def log_order_nutrition(session: AsyncSession, order: CustomerOrder, location: Location) -> None:
    from app.services.analytics import local_today

    exists = await session.scalar(select(NutritionEntry.id).where(NutritionEntry.customer_order_id == order.id))
    if exists:
        return
    for ln in order.lines:
        if ln.get("calories") is None:
            continue
        qty = ln.get("qty") or 1
        session.add(NutritionEntry(
            user_id=order.customer_user_id, day=local_today(location), name=f"{ln['name']} ({location.name})",
            calories=ln["calories"] * qty,
            protein_g=(ln.get("protein_g") or 0) * qty, carbs_g=(ln.get("carbs_g") or 0) * qty,
            fat_g=(ln.get("fat_g") or 0) * qty, source="order", customer_order_id=order.id))


async def add_tip(session: AsyncSession, order: CustomerOrder, user: User, tip_cents: int) -> CustomerOrder:
    """Tips can be added or raised until a day after the order is completed; all of it goes to the driver."""
    if order.fulfilment != "delivery":
        raise OrderError("Tips in the app are for delivery drivers")
    if order.status == "cancelled":
        raise OrderError("This order was cancelled")
    if order.completed_at and utcnow() - order.completed_at > timedelta(days=1):
        raise OrderError("Tips can be added up to 24 hours after delivery")
    tip = int(tip_cents)
    if not 0 < tip <= 50_000 or tip <= order.tip_cents:
        raise OrderError("Enter a tip higher than the current one (up to $500)")
    extra = tip - order.tip_cents
    result = await payments.charge_tip(order, user, extra)
    order.tip_cents = tip
    order.total_cents += extra
    delivery = await session.scalar(select(Delivery).where(Delivery.customer_order_id == order.id))
    if delivery is not None:
        delivery.tip_cents = tip
        if delivery.driver_id:
            from app.models import Driver

            driver = await session.get(Driver, delivery.driver_id)
            if driver is not None and driver.user_id:
                await create_user_alert(session, driver.user_id, kind="tip", title="You got a tip",
                                        body=f"{fmt_money(extra)} tip added for order {order.id[:8]}.",
                                        data={"delivery_id": delivery.id, "status": result["status"]},
                                        dedupe_key=f"tip:{order.id}:{tip}")
    await session.commit()
    return order


def suggested_tips(subtotal_cents: int) -> list[dict]:
    """Tip choices shown after delivery: 10/15/20% with a $3 minimum suggestion."""
    out = []
    for pct in (10, 15, 20):
        cents = max(300, round(subtotal_cents * pct / 100 / 25) * 25)
        out.append({"pct": pct, "cents": cents, "label": fmt_money(cents)})
    return out
