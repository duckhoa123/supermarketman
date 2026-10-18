"""Restaurant Manager: public profile, forecast and reports, labour, suppliers and purchase orders,
guests, bookings, marketing insights and orders from the customer app."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import Booking, CustomerOrder, Delivery, Guest, Item, Location, PurchaseOrder, StaffShift, Supplier, utcnow
from app.money import fmt_money
from app.services import analytics, forecast, guests, marketing, ordering, purchasing, reports
from app.services.geo import WEEKDAY_KEYS
from app.tenancy import require_location, require_manager

router = APIRouter(prefix="/locations/{location_id}", tags=["restaurant-operations"])


def _422(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


# --- Public profile (customer app and delivery) -----------------------------------------------------


class ProfileIn(BaseModel):
    address: str | None = Field(default=None, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    phone: str | None = Field(default=None, max_length=32)
    cuisine: str | None = Field(default=None, max_length=100)
    opening_hours: dict[str, list[list[str]]] | None = None
    listed: bool | None = None
    accepts_orders: bool | None = None
    accepts_delivery: bool | None = None
    accepts_bookings: bool | None = None
    tax_rate_bp: int | None = Field(default=None, ge=0, le=2500)

    @field_validator("opening_hours")
    @classmethod
    def _hours(cls, value):
        if value is None:
            return value
        for day, ranges in value.items():
            if day not in WEEKDAY_KEYS:
                raise ValueError(f"Unknown day {day}; use mon..sun")
            for r in ranges:
                if len(r) != 2 or not all(len(t) == 5 and t[2] == ":" for t in r):
                    raise ValueError("Each range is [\"HH:MM\", \"HH:MM\"]")
        return value


def profile_out(loc: Location) -> dict:
    return {k: getattr(loc, k) for k in ("id", "name", "timezone", "currency", "address", "lat", "lng", "phone",
                                         "cuisine", "opening_hours", "listed", "accepts_orders", "accepts_delivery",
                                         "accepts_bookings", "tax_rate_bp")}


@router.get("/profile")
async def get_profile(location: Location = Depends(require_location)):
    return profile_out(location)


@router.put("/profile")
async def put_profile(body: ProfileIn, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(location, key, value)
    if (location.listed or location.accepts_delivery) and (location.lat is None or location.lng is None):
        raise HTTPException(422, "Add the restaurant's map position (lat/lng) before listing it or offering delivery")
    await session.commit()
    return profile_out(location)


# --- Forecast, reports, cost control ----------------------------------------------------------------


@router.get("/forecast")
async def get_forecast(days: int = Query(7, ge=1, le=14), location: Location = Depends(require_location),
                       session: AsyncSession = Depends(get_session)):
    return await forecast.forecast(session, location, days)


@router.get("/reports/{kind}")
async def get_report(kind: str, location: Location = Depends(require_location),
                     session: AsyncSession = Depends(get_session)):
    try:
        r = await reports.report(session, location, kind)
    except ValueError as exc:
        raise _422(exc) from exc
    r["text"] = reports.render(r)[1]
    return r


@router.get("/labour")
async def get_labour(period: str = "last_7_days", location: Location = Depends(require_location),
                     session: AsyncSession = Depends(get_session)):
    try:
        return await reports.labour(session, location, period)
    except ValueError as exc:
        raise _422(exc) from exc


@router.get("/food-cost")
async def get_food_cost(period: str = "last_7_days", location: Location = Depends(require_location),
                        session: AsyncSession = Depends(get_session)):
    try:
        return await reports.food_cost(session, location, period)
    except ValueError as exc:
        raise _422(exc) from exc


class ShiftIn(BaseModel):
    staff_name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=32)
    starts_at: datetime
    ends_at: datetime
    hourly_rate_cents: int = Field(ge=0, le=100_000)


def shift_out(sh: StaffShift, tz: str) -> dict:
    z = ZoneInfo(tz)
    return {"id": sh.id, "staff_name": sh.staff_name, "role": sh.role, "starts_at": sh.starts_at.isoformat(),
            "ends_at": sh.ends_at.isoformat(), "local": f"{sh.starts_at.astimezone(z):%a %d %b %H:%M}-"
            f"{sh.ends_at.astimezone(z):%H:%M}", "hours": round((sh.ends_at - sh.starts_at).total_seconds() / 3600, 2),
            "hourly_rate": fmt_money(sh.hourly_rate_cents), "hourly_rate_cents": sh.hourly_rate_cents}


@router.get("/shifts")
async def list_shifts(start: date | None = None, days: int = Query(7, ge=1, le=62),
                      location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    tz = ZoneInfo(location.timezone)
    first = start or analytics.local_today(location) - timedelta(days=analytics.local_today(location).weekday())
    s = datetime.combine(first, time(0), tzinfo=tz)
    rows = await session.scalars(select(StaffShift).where(
        StaffShift.location_id == location.id, StaffShift.starts_at >= s, StaffShift.starts_at < s + timedelta(days=days)
    ).order_by(StaffShift.starts_at))
    return {"from": first.isoformat(), "shifts": [shift_out(x, location.timezone) for x in rows]}


@router.post("/shifts")
async def add_shift(body: ShiftIn, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    if body.starts_at.tzinfo is None or body.ends_at.tzinfo is None:
        raise HTTPException(422, "Times need a time zone, e.g. 2026-10-18T17:00:00-07:00")
    if not timedelta(minutes=15) <= body.ends_at - body.starts_at <= timedelta(hours=16):
        raise HTTPException(422, "A shift is between 15 minutes and 16 hours")
    sh = StaffShift(location_id=location.id, **body.model_dump())
    session.add(sh)
    await session.commit()
    return shift_out(sh, location.timezone)


@router.delete("/shifts/{shift_id}")
async def delete_shift(shift_id: str, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    sh = await session.get(StaffShift, shift_id)
    if sh is None or sh.location_id != location.id:
        raise HTTPException(404, "Shift not found")
    await session.delete(sh)
    await session.commit()
    return {"ok": True}


# --- Suppliers and purchase orders --------------------------------------------------------------------


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=1000)


def supplier_out(x: Supplier) -> dict:
    return {"id": x.id, "name": x.name, "email": x.email, "phone": x.phone, "notes": x.notes}


@router.get("/suppliers")
async def list_suppliers(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Supplier).where(Supplier.location_id == location.id).order_by(Supplier.name))
    return [supplier_out(x) for x in rows]


@router.post("/suppliers")
async def add_supplier(body: SupplierIn, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    x = Supplier(location_id=location.id, **body.model_dump())
    session.add(x)
    await session.commit()
    return supplier_out(x)


@router.patch("/suppliers/{supplier_id}")
async def edit_supplier(supplier_id: str, body: SupplierIn, location: Location = Depends(require_manager),
                        session: AsyncSession = Depends(get_session)):
    x = await session.get(Supplier, supplier_id)
    if x is None or x.location_id != location.id:
        raise HTTPException(404, "Supplier not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(x, k, v)
    await session.commit()
    return supplier_out(x)


async def _po(session: AsyncSession, location: Location, po_id: str) -> PurchaseOrder:
    po = await session.get(PurchaseOrder, po_id)
    if po is None or po.location_id != location.id:
        raise HTTPException(404, "Purchase order not found")
    return po


@router.get("/purchase-orders")
async def list_pos(status: str | None = None, location: Location = Depends(require_location),
                   session: AsyncSession = Depends(get_session)):
    return await purchasing.list_orders(session, location, status)


@router.post("/purchase-orders/draft")
async def draft_pos(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    result = await purchasing.draft_from_low_stock(session, location)
    await session.commit()
    return {"drafts": [d.id for d in result["drafts"]], "no_supplier": result["no_supplier"]}


class POLine(BaseModel):
    item_id: str | None = None
    name: str = Field(min_length=1, max_length=300)
    qty: float = Field(gt=0, le=100_000)
    unit: str | None = Field(default=None, max_length=32)


class POPatch(BaseModel):
    lines: list[POLine] | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/purchase-orders/{po_id}")
async def edit_po(po_id: str, body: POPatch, location: Location = Depends(require_manager),
                  session: AsyncSession = Depends(get_session)):
    po = await _po(session, location, po_id)
    if po.status != "draft":
        raise HTTPException(409, "Only drafts can be edited")
    if body.lines is not None:
        po.lines = [ln.model_dump() for ln in body.lines]
    if body.note is not None:
        po.note = body.note
    await session.commit()
    return {"ok": True}


class ApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/purchase-orders/{po_id}/approve")
async def approve_po(po_id: str, body: ApproveIn | None = None, location: Location = Depends(require_manager),
                     user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    po = await _po(session, location, po_id)
    try:
        await purchasing.approve(session, po, user.id, body.note if body else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "status": po.status}


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(po_id: str, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    po = await _po(session, location, po_id)
    if po.status in ("sent", "received"):
        raise HTTPException(409, "Already sent; contact the supplier to cancel")
    po.status = "cancelled"
    await session.commit()
    return {"ok": True}


@router.post("/purchase-orders/{po_id}/received")
async def receive_po(po_id: str, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    """Mark delivered and add the quantities to stock (POS-synced stock will be corrected on the next sync)."""
    po = await _po(session, location, po_id)
    if po.status not in ("approved", "sent"):
        raise HTTPException(409, f"Can't receive a {po.status} order")
    for line in po.lines:
        item = await session.get(Item, line.get("item_id")) if line.get("item_id") else None
        if item is not None and item.location_id == location.id and item.stock_qty is not None:
            item.stock_qty += float(line["qty"])
    po.status = "received"
    await session.commit()
    return {"ok": True}


# --- Guests and bookings --------------------------------------------------------------------------------


class GuestIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=32)
    marketing_consent: bool = False
    notes: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list)


@router.get("/guests")
async def list_guests(search: str | None = None, sort: str = "recent", location: Location = Depends(require_location),
                      session: AsyncSession = Depends(get_session)):
    return await guests.list_guests(session, location, search, sort)


@router.post("/guests")
async def add_guest(body: GuestIn, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    """Add a guest the restaurant met in person. Only with their consent to keep their details."""
    g = Guest(location_id=location.id, visits=0, total_spend_cents=0, **body.model_dump())
    if body.marketing_consent:
        g.consent_at = utcnow()
    session.add(g)
    await session.commit()
    return guests.guest_out(g, location.currency)


@router.patch("/guests/{guest_id}")
async def edit_guest(guest_id: str, body: GuestIn, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    g = await session.get(Guest, guest_id)
    if g is None or g.location_id != location.id:
        raise HTTPException(404, "Guest not found")
    changes = body.model_dump(exclude_unset=True)
    if "marketing_consent" in changes and changes["marketing_consent"] != g.marketing_consent:
        g.consent_at = utcnow() if changes["marketing_consent"] else None
    for k, v in changes.items():
        setattr(g, k, v)
    await session.commit()
    return guests.guest_out(g, location.currency)


@router.delete("/guests/{guest_id}")
async def delete_guest(guest_id: str, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    """Guests can ask to be removed (BC PIPA); this deletes their details."""
    g = await session.get(Guest, guest_id)
    if g is None or g.location_id != location.id:
        raise HTTPException(404, "Guest not found")
    await session.delete(g)
    await session.commit()
    return {"ok": True}


@router.get("/bookings")
async def list_bookings(day: date | None = None, days: int = Query(1, ge=1, le=31),
                        location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    first = day or analytics.local_today(location)
    start = datetime.combine(first, time(0), tzinfo=ZoneInfo(location.timezone))
    return {"from": first.isoformat(), "bookings": await guests.list_bookings(
        session, location, start, start + timedelta(days=days))}


class BookingIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    party_size: int = Field(ge=1, le=50)
    starts_at: datetime
    source: str = Field(default="staff", pattern="^(staff|phone|walk_in)$")
    notes: str | None = Field(default=None, max_length=1000)
    guest_id: str | None = None


@router.post("/bookings")
async def add_booking(body: BookingIn, location: Location = Depends(require_location),
                      session: AsyncSession = Depends(get_session)):
    if body.starts_at.tzinfo is None:
        raise HTTPException(422, "starts_at needs a time zone")
    if body.guest_id:
        g = await session.get(Guest, body.guest_id)
        if g is None or g.location_id != location.id:
            raise HTTPException(404, "Guest not found")
    b = Booking(location_id=location.id, status="confirmed", **body.model_dump())
    session.add(b)
    await session.commit()
    return guests.booking_out(b, location.timezone)


class BookingStatusIn(BaseModel):
    status: str = Field(pattern="^(requested|confirmed|seated|completed|cancelled|no_show)$")


@router.patch("/bookings/{booking_id}")
async def booking_status(booking_id: str, body: BookingStatusIn, location: Location = Depends(require_location),
                         session: AsyncSession = Depends(get_session)):
    b = await session.get(Booking, booking_id)
    if b is None or b.location_id != location.id:
        raise HTTPException(404, "Booking not found")
    await guests.set_booking_status(session, location, b, body.status)
    return guests.booking_out(b, location.timezone)


@router.get("/marketing")
async def get_marketing(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    return await marketing.insights(session, location)


# --- Orders from the customer app --------------------------------------------------------------------


@router.get("/app-orders")
async def app_orders(active_only: bool = True, location: Location = Depends(require_location),
                     session: AsyncSession = Depends(get_session)):
    stmt = select(CustomerOrder).where(CustomerOrder.location_id == location.id)
    if active_only:
        stmt = stmt.where(CustomerOrder.status.in_(("placed", "accepted", "ready", "out_for_delivery")))
    else:
        stmt = stmt.where(CustomerOrder.placed_at >= utcnow() - timedelta(days=7))
    rows = list(await session.scalars(stmt.order_by(CustomerOrder.placed_at.desc()).limit(100)))
    out = []
    for o in rows:
        d = await session.scalar(select(Delivery).where(Delivery.customer_order_id == o.id))
        out.append(ordering.order_out(o, location.currency, delivery=d, for_restaurant=True))
    return {"orders": out}


class OrderStatusIn(BaseModel):
    status: str = Field(pattern="^(accepted|ready|picked_up|cancelled)$")


@router.post("/app-orders/{order_id}/status")
async def app_order_status(order_id: str, body: OrderStatusIn, location: Location = Depends(require_location),
                           session: AsyncSession = Depends(get_session)):
    o = await session.get(CustomerOrder, order_id)
    if o is None or o.location_id != location.id:
        raise HTTPException(404, "Order not found")
    try:
        await ordering.set_status(session, o, body.status, "restaurant")
    except ordering.OrderError as exc:
        raise HTTPException(409, str(exc)) from exc
    d = await session.scalar(select(Delivery).where(Delivery.customer_order_id == o.id))
    return ordering.order_out(o, location.currency, delivery=d, for_restaurant=True)
