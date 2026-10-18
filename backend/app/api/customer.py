"""Customer app: profile and goals, restaurant search and menus, orders, tips, bookings, nutrition log."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import Booking, CustomerOrder, Delivery, Location, User
from app.services import customer, guests, ordering

router = APIRouter(prefix="/customer", tags=["customer"])


class ProfileIn(BaseModel):
    home_address: str | None = Field(default=None, max_length=300)
    home_lat: float | None = Field(default=None, ge=-90, le=90)
    home_lng: float | None = Field(default=None, ge=-180, le=180)
    diet_tags: list[str] | None = None
    calories_goal: float | None = Field(default=None, ge=800, le=6000)
    protein_goal_g: float | None = Field(default=None, ge=0, le=500)
    carbs_goal_g: float | None = Field(default=None, ge=0, le=1000)
    fat_goal_g: float | None = Field(default=None, ge=0, le=400)


@router.get("/profile")
async def get_profile(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    p = await customer.profile(session, user.id)
    await session.commit()
    return customer.profile_out(p)


@router.put("/profile")
async def put_profile(body: ProfileIn, user: AuthUser = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    p = await customer.profile(session, user.id)
    changes = body.model_dump(exclude_unset=True)
    if "diet_tags" in changes:
        changes["diet_tags"] = sorted({t.strip().lower().replace(" ", "_") for t in changes["diet_tags"] or []
                                       if t.strip()})
    for k, v in changes.items():
        setattr(p, k, v)
    await session.commit()
    return customer.profile_out(p)


async def _listed(session: AsyncSession, location_id: str, user_id: str) -> Location:
    loc = await session.get(Location, location_id)
    if not await customer.visible(session, loc, user_id):
        raise HTTPException(404, "Restaurant not found")
    return loc


@router.get("/restaurants")
async def search(lat: float | None = None, lng: float | None = None, q: str | None = None,
                 radius_km: float = Query(5, ge=0.5, le=20), open_now: bool = False,
                 fulfilment: str | None = Query(None, pattern="^(pickup|delivery|booking)$"),
                 diet: str | None = None, max_calories: float | None = None,
                 user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if lat is None or lng is None:
        p = await customer.profile(session, user.id)
        lat, lng = p.home_lat, p.home_lng
    return await customer.search(session, viewer_id=user.id, lat=lat, lng=lng, query=q, radius_km=radius_km,
                                 open_now=open_now,
                                 fulfilment=fulfilment, diet_tags=[t for t in (diet or "").split(",") if t],
                                 max_calories=max_calories)


@router.get("/restaurants/{location_id}/menu")
async def menu(location_id: str, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await customer.menu(session, await _listed(session, location_id, user.id))


class LineIn(BaseModel):
    item_id: str
    qty: int = Field(default=1, ge=1, le=20)


class OrderIn(BaseModel):
    restaurant_id: str
    items: list[LineIn] = Field(min_length=1, max_length=30)
    fulfilment: str = Field(pattern="^(pickup|delivery)$")
    notes: str | None = Field(default=None, max_length=500)
    tip_cents: int = Field(default=0, ge=0, le=50_000)
    share_contact: bool = False  # join the restaurant's guest list (and its marketing only with marketing_consent)
    marketing_consent: bool = False


async def _dropoff(session: AsyncSession, user_id: str, fulfilment: str) -> tuple[tuple[float, float] | None, str | None]:
    if fulfilment != "delivery":
        return None, None
    p = await customer.profile(session, user_id)
    if p.home_lat is None or p.home_lng is None:
        raise HTTPException(422, "Add your delivery address in your profile first")
    return (p.home_lat, p.home_lng), p.home_address


@router.post("/orders/quote")
async def quote(body: OrderIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    loc = await _listed(session, body.restaurant_id, user.id)
    dropoff, _ = await _dropoff(session, user.id, body.fulfilment)
    try:
        q = await ordering.quote(session, loc, [i.model_dump() for i in body.items], body.fulfilment, dropoff)
    except ordering.OrderError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**q.out(loc.currency), "tip_suggestions": ordering.suggested_tips(q.subtotal_cents)
            if body.fulfilment == "delivery" else []}


@router.post("/orders")
async def place(body: OrderIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    loc = await _listed(session, body.restaurant_id, user.id)
    dropoff, address = await _dropoff(session, user.id, body.fulfilment)
    account = await session.get(User, user.id)
    try:
        order, payment = await ordering.place(
            session, loc, account, items=[i.model_dump() for i in body.items], fulfilment=body.fulfilment,
            dropoff=dropoff, address=address, notes=body.notes, share_contact=body.share_contact,
            tip_cents=body.tip_cents if body.fulfilment == "delivery" else 0)
    except ordering.OrderError as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.share_contact:
        await guests.guest_for_customer(session, loc, account, marketing_consent=body.marketing_consent)
        await session.commit()
    return {"order": ordering.order_out(order, loc.currency, restaurant=loc.name),
            "payment": {k: payment.get(k) for k in ("status", "client_secret", "message")}}


async def _my_order(session: AsyncSession, user_id: str, order_id: str) -> CustomerOrder:
    o = await session.get(CustomerOrder, order_id)
    if o is None or o.customer_user_id != user_id:
        raise HTTPException(404, "Order not found")
    return o


@router.get("/orders")
async def my_orders(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = list(await session.scalars(select(CustomerOrder).where(CustomerOrder.customer_user_id == user.id)
                                      .order_by(CustomerOrder.placed_at.desc()).limit(30)))
    out = []
    for o in rows:
        loc = await session.get(Location, o.location_id)
        d = await session.scalar(select(Delivery).where(Delivery.customer_order_id == o.id))
        out.append(ordering.order_out(o, loc.currency, restaurant=loc.name, delivery=d))
    return {"orders": out}


@router.get("/orders/{order_id}")
async def my_order(order_id: str, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    o = await _my_order(session, user.id, order_id)
    loc = await session.get(Location, o.location_id)
    d = await session.scalar(select(Delivery).where(Delivery.customer_order_id == o.id))
    out = ordering.order_out(o, loc.currency, restaurant=loc.name, delivery=d)
    if o.fulfilment == "delivery":
        out["tip_suggestions"] = ordering.suggested_tips(o.subtotal_cents)
    return out


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: str, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    o = await _my_order(session, user.id, order_id)
    try:
        await ordering.set_status(session, o, "cancelled", "customer")
    except ordering.OrderError as exc:
        raise HTTPException(409, "The restaurant has already started this order; contact them to cancel") from exc
    return {"ok": True}


class TipIn(BaseModel):
    tip_cents: int = Field(gt=0, le=50_000)


@router.post("/orders/{order_id}/tip")
async def tip(order_id: str, body: TipIn, user: AuthUser = Depends(current_user),
              session: AsyncSession = Depends(get_session)):
    o = await _my_order(session, user.id, order_id)
    account = await session.get(User, user.id)
    try:
        await ordering.add_tip(session, o, account, body.tip_cents)
    except ordering.OrderError as exc:
        raise HTTPException(409, str(exc)) from exc
    loc = await session.get(Location, o.location_id)
    return ordering.order_out(o, loc.currency, restaurant=loc.name)


class BookingIn(BaseModel):
    restaurant_id: str
    party_size: int = Field(ge=1, le=20)
    starts_at: datetime
    notes: str | None = Field(default=None, max_length=500)
    share_contact: bool = True
    marketing_consent: bool = False


@router.post("/bookings")
async def book(body: BookingIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    from app.models import utcnow
    from app.services.alerts import create_alert

    loc = await _listed(session, body.restaurant_id, user.id)
    if not loc.accepts_bookings:
        raise HTTPException(409, f"{loc.name} doesn't take bookings in the app")
    if body.starts_at.tzinfo is None:
        raise HTTPException(422, "starts_at needs a time zone")
    if body.starts_at < utcnow():
        raise HTTPException(422, "That time has passed")
    if not await guests.seats_free(session, loc, body.starts_at, body.party_size):
        raise HTTPException(409, "That time is fully booked; try 30-60 minutes earlier or later")
    account = await session.get(User, user.id)
    guest = (await guests.guest_for_customer(session, loc, account, marketing_consent=body.marketing_consent)
             if body.share_contact else None)
    b = Booking(location_id=loc.id, customer_user_id=user.id, guest_id=guest.id if guest else None,
                name=account.name or account.email.split("@")[0], phone=account.phone, party_size=body.party_size,
                starts_at=body.starts_at, status="requested", source="customer_app", notes=body.notes)
    session.add(b)
    await session.flush()
    out = guests.booking_out(b, loc.timezone, loc.name)
    await create_alert(session, loc, kind="new_booking", title=f"Booking request: {b.party_size} people",
                       body=f"{b.name}, {out['local_time']}" + (f". Note: {b.notes}" if b.notes else ""),
                       data={"booking_id": b.id}, dedupe_key=f"booking:{b.id}", channels=["push"])
    await session.commit()
    return out


@router.get("/bookings")
async def my_bookings(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = list(await session.scalars(select(Booking).where(Booking.customer_user_id == user.id)
                                      .order_by(Booking.starts_at.desc()).limit(30)))
    out = []
    for b in rows:
        loc = await session.get(Location, b.location_id)
        out.append(guests.booking_out(b, loc.timezone, loc.name))
    return {"bookings": out}


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, user: AuthUser = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    b = await session.get(Booking, booking_id)
    if b is None or b.customer_user_id != user.id:
        raise HTTPException(404, "Booking not found")
    if b.status not in ("requested", "confirmed"):
        raise HTTPException(409, f"This booking is {b.status}")
    loc = await session.get(Location, b.location_id)
    await guests.set_booking_status(session, loc, b, "cancelled")
    return {"ok": True}


@router.get("/nutrition")
async def nutrition(day: date | None = None, user: AuthUser = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    out = await customer.nutrition_day(session, user.id, day or customer.local_date())
    await session.commit()
    return out


class FoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    day: date | None = None
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)


@router.post("/nutrition")
async def log_food(body: FoodIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    entry = await customer.log_food(session, user.id, body.day or customer.local_date(), body.name, body.model_dump())
    return {"id": entry.id}


@router.delete("/nutrition/{entry_id}")
async def delete_food(entry_id: str, user: AuthUser = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    from app.models import NutritionEntry

    e = await session.get(NutritionEntry, entry_id)
    if e is None or e.user_id != user.id:
        raise HTTPException(404, "Entry not found")
    await session.delete(e)
    await session.commit()
    return {"ok": True}
