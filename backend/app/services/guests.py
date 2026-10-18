"""Guest list and bookings, shared between the restaurant and the customer app."""

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, DiningTable, Guest, Location, User, utcnow

BOOKING_STATUSES = ("requested", "confirmed", "seated", "completed", "cancelled", "no_show")
ACTIVE_BOOKING = ("requested", "confirmed", "seated")
SITTING = timedelta(minutes=90)  # how long a table is assumed busy per booking


async def guest_for_customer(session: AsyncSession, location: Location, user: User, *,
                             marketing_consent: bool | None = None) -> Guest:
    """Find or create the guest-list entry for a customer who agreed to share their details."""
    guest = await session.scalar(select(Guest).where(Guest.location_id == location.id, Guest.user_id == user.id))
    if guest is None:
        guest = Guest(location_id=location.id, user_id=user.id, name=user.name or user.email.split("@")[0],
                      email=user.email, phone=user.phone, visits=0, total_spend_cents=0, tags=[])
        session.add(guest)
    if marketing_consent is not None and marketing_consent != guest.marketing_consent:
        guest.marketing_consent = marketing_consent
        guest.consent_at = utcnow() if marketing_consent else None
    await session.flush()
    return guest


async def record_visit(session: AsyncSession, guest: Guest, spend_cents: int = 0, when: datetime | None = None) -> None:
    guest.visits = (guest.visits or 0) + 1
    guest.total_spend_cents = (guest.total_spend_cents or 0) + spend_cents
    guest.last_visit_at = when or utcnow()


def guest_out(g: Guest, currency: str) -> dict:
    from app.money import fmt_money

    return {"id": g.id, "name": g.name, "email": g.email, "phone": g.phone, "visits": g.visits,
            "total_spend": fmt_money(g.total_spend_cents, currency),
            "last_visit": g.last_visit_at.date().isoformat() if g.last_visit_at else None,
            "marketing_consent": g.marketing_consent, "notes": g.notes, "tags": g.tags or [],
            "linked_customer": g.user_id is not None}


async def list_guests(session: AsyncSession, location: Location, search: str | None = None,
                      sort: str = "recent", limit: int = 50) -> dict:
    stmt = select(Guest).where(Guest.location_id == location.id)
    if search:
        stmt = stmt.where(or_(Guest.name.ilike(f"%{search}%"), Guest.email.ilike(f"%{search}%"),
                              Guest.phone.ilike(f"%{search}%")))
    order = {"visits": Guest.visits.desc(), "spend": Guest.total_spend_cents.desc()}.get(
        sort, Guest.last_visit_at.desc())
    guests = list(await session.scalars(stmt.order_by(order).limit(min(limit, 100))))
    total = await session.scalar(select(func.count(Guest.id)).where(Guest.location_id == location.id))
    return {"total": total or 0, "guests": [guest_out(g, location.currency) for g in guests]}


async def seats_free(session: AsyncSession, location: Location, starts_at: datetime, party_size: int) -> bool:
    """Rough capacity check: covers already booked around that time must leave room for this party.
    With no tables set up, capacity is unknown and every request is accepted for staff to confirm."""
    seats = await session.scalar(select(func.sum(DiningTable.seats)).where(DiningTable.location_id == location.id))
    if not seats:
        return True
    booked = await session.scalar(select(func.sum(Booking.party_size)).where(
        Booking.location_id == location.id, Booking.status.in_(ACTIVE_BOOKING),
        Booking.starts_at > starts_at - SITTING, Booking.starts_at < starts_at + SITTING)) or 0
    return booked + party_size <= seats


def booking_out(b: Booking, tz: str, location_name: str | None = None) -> dict:
    from zoneinfo import ZoneInfo

    out = {"id": b.id, "name": b.name, "phone": b.phone, "party_size": b.party_size,
           "starts_at": b.starts_at.isoformat(),
           "local_time": b.starts_at.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M"),
           "status": b.status, "source": b.source, "notes": b.notes, "guest_id": b.guest_id}
    if location_name:
        out["restaurant"] = location_name
    return out


async def list_bookings(session: AsyncSession, location: Location, start: datetime, end: datetime) -> list[dict]:
    rows = await session.scalars(select(Booking).where(
        Booking.location_id == location.id, Booking.starts_at >= start, Booking.starts_at < end
    ).order_by(Booking.starts_at))
    return [booking_out(b, location.timezone) for b in rows]


async def set_booking_status(session: AsyncSession, location: Location, booking: Booking, status: str) -> Booking:
    from app.services.alerts import create_user_alert

    if status not in BOOKING_STATUSES:
        raise ValueError("Unknown status")
    previous = booking.status
    booking.status = status
    if status == "seated" and previous != "seated" and booking.guest_id:
        guest = await session.get(Guest, booking.guest_id)
        if guest is not None:
            await record_visit(session, guest)
    if booking.customer_user_id and status in ("confirmed", "cancelled") and status != previous:
        when = booking_out(booking, location.timezone)["local_time"]
        await create_user_alert(
            session, booking.customer_user_id, kind="booking_update",
            title=f"Booking {status} at {location.name}",
            body=f"Table for {booking.party_size}, {when}." if status == "confirmed"
            else f"Your booking for {when} was cancelled. Contact the restaurant if this is unexpected.",
            data={"booking_id": booking.id}, dedupe_key=f"booking:{booking.id}:{status}")
    await session.commit()
    return booking
