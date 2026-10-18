"""The in-house delivery fleet: drivers we employ, their shifts, dispatch, busy alerts and pay.

Employed drivers are paid for every hour on shift, so the system works to keep each one busy:
assign the nearest free driver, batch orders from the same restaurant, and call in on-call drivers
only when deliveries pile up.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CustomerOrder, Delivery, Driver, DriverShift, Location, User, utcnow
from app.money import fmt_money
from app.services import geo
from app.services.alerts import create_user_alert

ACTIVE = ("assigned", "picked_up")
STALE_POSITION = timedelta(minutes=20)  # older positions are treated as "at the hub"


async def driver_for_user(session: AsyncSession, user_id: str, email: str) -> Driver | None:
    """The driver record for this account. An invite (email only) is claimed on first sign-in."""
    driver = await session.scalar(select(Driver).where(Driver.user_id == user_id))
    if driver is None and email:
        driver = await session.scalar(select(Driver).where(
            func.lower(Driver.email) == email.lower(), Driver.user_id.is_(None)))
        if driver is not None:
            driver.user_id = user_id
            await session.commit()
    return driver if driver is not None and driver.active else None


async def open_shift(session: AsyncSession, driver: Driver) -> DriverShift | None:
    return await session.scalar(select(DriverShift).where(
        DriverShift.driver_id == driver.id, DriverShift.ends_at.is_(None)))


async def start_shift(session: AsyncSession, driver: Driver, lat: float | None = None,
                      lng: float | None = None) -> DriverShift:
    shift = await open_shift(session, driver)
    if shift is None:
        shift = DriverShift(driver_id=driver.id, starts_at=utcnow())
        session.add(shift)
    driver.status = "on_shift"
    if lat is not None and lng is not None:
        update_position(driver, lat, lng)
    await session.flush()
    await sweep(session)
    await session.commit()
    return shift


async def end_shift(session: AsyncSession, driver: Driver, on_call: bool = False) -> DriverShift | None:
    active = await session.scalar(select(func.count(Delivery.id)).where(
        Delivery.driver_id == driver.id, Delivery.status.in_(ACTIVE)))
    if active:
        raise ValueError("Finish your current deliveries before ending the shift")
    shift = await open_shift(session, driver)
    if shift is not None:
        shift.ends_at = utcnow()
    driver.status = "on_call" if on_call else "off"
    await session.commit()
    return shift


def update_position(driver: Driver, lat: float, lng: float) -> None:
    driver.last_lat, driver.last_lng, driver.last_seen_at = lat, lng, utcnow()


def _position(driver: Driver) -> tuple[float, float]:
    s = get_settings()
    if driver.last_lat is None or driver.last_seen_at is None or utcnow() - driver.last_seen_at > STALE_POSITION:
        return s.delivery_hub_lat, s.delivery_hub_lng
    return driver.last_lat, driver.last_lng


async def driver_states(session: AsyncSession) -> list[tuple[Driver, geo.DriverState]]:
    drivers = list(await session.scalars(select(Driver).where(Driver.active.is_(True), Driver.status == "on_shift")))
    out = []
    for d in drivers:
        jobs = list(await session.scalars(select(Delivery).where(Delivery.driver_id == d.id,
                                                                 Delivery.status.in_(ACTIVE))))
        lat, lng = _position(d)
        out.append((d, geo.DriverState(
            id=d.id, lat=lat, lng=lng, vehicle=d.vehicle, active=len(jobs),
            heading_to=tuple(j.location_id for j in jobs if j.status == "assigned"))))
    return out


async def assign(session: AsyncSession, delivery: Delivery, driver: Driver | None = None,
                 reason: str | None = None) -> Driver | None:
    """Give a ready delivery to a driver (the best one, or the one a dispatcher picked)."""
    s = get_settings()
    if delivery.status != "waiting":
        return None
    order = await session.get(CustomerOrder, delivery.customer_order_id)
    if order is None or order.status != "ready":
        return None  # assigned once the kitchen marks it ready, so drivers don't wait around
    states = await driver_states(session)
    state = None
    if driver is None:
        picked = geo.choose_driver(delivery.location_id, delivery.pickup_lat, delivery.pickup_lng,
                                   [st for _, st in states], s.driver_max_active_deliveries, s.delivery_radius_km)
        if picked is None:
            return None
        state, reason = picked
        driver = next(d for d, st in states if st.id == state.id)
    else:
        state = next((st for d, st in states if d.id == driver.id), None)
        if state is None:
            raise ValueError("That driver isn't on shift")
        reason = reason or "assigned by dispatcher"
    now = utcnow()
    to_pickup = geo.haversine_km(state.lat, state.lng, delivery.pickup_lat, delivery.pickup_lng)
    eta = now + timedelta(minutes=geo.delivery_eta_minutes(to_pickup, delivery.distance_km, driver.vehicle))
    delivery.driver_id, delivery.status, delivery.assigned_at, delivery.eta_at = driver.id, "assigned", now, eta
    order.eta_at = eta
    location = await session.get(Location, delivery.location_id)
    if driver.user_id:
        await create_user_alert(
            session, driver.user_id, kind="delivery_job", title=f"New pickup: {location.name}",
            body=f"{location.address or location.name} -> {order.delivery_address or 'customer'} "
            f"({delivery.distance_km:.1f} km). {reason.capitalize()}.",
            data={"delivery_id": delivery.id}, dedupe_key=f"job:{delivery.id}:{driver.id}", channels=["push", "sms"])
    await session.flush()
    return driver


async def sweep(session: AsyncSession) -> dict:
    """Assign every ready delivery still waiting, then check whether the fleet needs more drivers."""
    waiting = list(await session.scalars(
        select(Delivery).join(CustomerOrder, CustomerOrder.id == Delivery.customer_order_id)
        .where(Delivery.status == "waiting", CustomerOrder.status == "ready").order_by(CustomerOrder.ready_at)))
    assigned = 0
    for d in waiting:
        assigned += (await assign(session, d)) is not None
    busy = await check_busy(session)
    await session.flush()
    return {"waiting": len(waiting), "assigned": assigned, "busy_alert": busy}


async def check_busy(session: AsyncSession) -> bool:
    """If deliveries per on-shift driver pass the limit, alert on-call drivers and dispatchers (once per 30 min)."""
    s = get_settings()
    open_jobs = await session.scalar(select(func.count(Delivery.id)).where(
        Delivery.status.in_(("waiting", *ACTIVE)))) or 0
    on_shift = await session.scalar(select(func.count(Driver.id)).where(
        Driver.active.is_(True), Driver.status == "on_shift")) or 0
    if open_jobs == 0 or (on_shift and open_jobs / on_shift < s.busy_deliveries_per_driver):
        return False
    hot = await busy_areas(session)
    where = ", ".join(a["restaurant"] for a in hot[:3]) or "the delivery area"
    now = utcnow()
    slot = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0).isoformat()
    recipients = list(await session.scalars(select(Driver).where(
        Driver.active.is_(True), Driver.user_id.is_not(None),
        (Driver.status == "on_call") | (Driver.role == "dispatcher"))))
    for d in recipients:
        await create_user_alert(
            session, d.user_id, kind="fleet_busy", title="Busy now: drivers needed",
            body=f"{open_jobs} open deliveries for {on_shift} driver(s) on shift, mostly around {where}. "
            "Open the app to start a shift.", dedupe_key=f"busy:{slot}", channels=["push", "sms"])
    return bool(recipients)


async def busy_areas(session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        select(Location.name, Location.lat, Location.lng, func.count(Delivery.id))
        .join(Delivery, Delivery.location_id == Location.id)
        .where(Delivery.status.in_(("waiting", "assigned")))
        .group_by(Location.id, Location.name, Location.lat, Location.lng)
        .order_by(func.count(Delivery.id).desc()))
    return [{"restaurant": n, "lat": lat, "lng": lng, "open_deliveries": int(c)} for n, lat, lng, c in rows]


async def advance(session: AsyncSession, driver: Driver, delivery: Delivery, status: str) -> Delivery:
    """Driver marks a delivery picked up or delivered."""
    from app.services import ordering

    if delivery.driver_id != driver.id:
        raise ValueError("This delivery isn't assigned to you")
    order = await session.get(CustomerOrder, delivery.customer_order_id)
    now = utcnow()
    if status == "picked_up" and delivery.status == "assigned":
        delivery.status, delivery.picked_up_at = "picked_up", now
        delivery.eta_at = now + timedelta(minutes=geo.travel_minutes(delivery.distance_km, driver.vehicle)
                                          + geo.HANDOFF_MIN)
        order.eta_at = delivery.eta_at
        await ordering.set_status(session, order, "out_for_delivery", "driver")
    elif status == "delivered" and delivery.status == "picked_up":
        delivery.status, delivery.delivered_at = "delivered", now
        await ordering.set_status(session, order, "delivered", "driver")
        await sweep(session)
        await session.commit()
    else:
        raise ValueError(f"Can't mark a {delivery.status} delivery as {status}")
    return delivery


def _overlap_hours(shifts: list[DriverShift], start: datetime, end: datetime) -> float:
    total = 0.0
    now = utcnow()
    for sh in shifts:
        s, e = max(sh.starts_at, start), min(sh.ends_at or now, end)
        if e > s:
            total += (e - s).total_seconds() / 3600
    return total


async def stats(session: AsyncSession, driver: Driver, start: datetime, end: datetime) -> dict:
    """Hours, deliveries per hour, on-time rate, wages and tips for a driver over a period."""
    shifts = list(await session.scalars(select(DriverShift).where(
        DriverShift.driver_id == driver.id, DriverShift.starts_at < end,
        (DriverShift.ends_at.is_(None)) | (DriverShift.ends_at > start))))
    hours = _overlap_hours(shifts, start, end)
    done = list(await session.scalars(select(Delivery).where(
        Delivery.driver_id == driver.id, Delivery.status == "delivered",
        Delivery.delivered_at >= start, Delivery.delivered_at < end)))
    on_time = [d for d in done if d.promised_at and d.delivered_at <= d.promised_at + timedelta(minutes=5)]
    tips = sum(d.tip_cents for d in done)
    wages = round(hours * driver.hourly_wage_cents)
    minutes = [(d.delivered_at - d.assigned_at).total_seconds() / 60 for d in done if d.assigned_at]
    return {
        "hours": round(hours, 2), "deliveries": len(done),
        "deliveries_per_hour": round(len(done) / hours, 2) if hours else None,
        "on_time_pct": round(len(on_time) / len(done) * 100) if done else None,
        "avg_minutes_assigned_to_delivered": round(sum(minutes) / len(minutes)) if minutes else None,
        "km": round(sum(d.distance_km for d in done), 1),
        "wages": fmt_money(wages), "tips": fmt_money(tips), "earnings": fmt_money(wages + tips),
        "earnings_cents": wages + tips,
    }


def delivery_out(d: Delivery, order: CustomerOrder | None, location: Location | None,
                 customer: User | None = None) -> dict:
    return {
        "id": d.id, "status": d.status, "restaurant": location.name if location else None,
        "pickup": {"address": location.address if location else None, "lat": d.pickup_lat, "lng": d.pickup_lng},
        "dropoff": {"address": order.delivery_address if order else None, "lat": d.dropoff_lat, "lng": d.dropoff_lng},
        "customer_name": (customer.name or "Customer") if customer else None,
        "notes": order.notes if order else None,
        "items": sum(ln.get("qty", 1) for ln in order.lines) if order else None,
        "distance_km": d.distance_km, "eta_at": d.eta_at.isoformat() if d.eta_at else None,
        "promised_at": d.promised_at.isoformat() if d.promised_at else None,
        "tip": fmt_money(d.tip_cents),
        "navigate_url": f"https://www.google.com/maps/dir/?api=1&destination="
        f"{d.pickup_lat if d.status == 'assigned' else d.dropoff_lat},"
        f"{d.pickup_lng if d.status == 'assigned' else d.dropoff_lng}",
    }


async def batching_suggestions(session: AsyncSession) -> list[dict]:
    """Waiting deliveries that could share a trip: same restaurant, or drop-offs within 1.5 km."""
    waiting = list(await session.scalars(select(Delivery).where(Delivery.status == "waiting")))
    out = []
    for i, a in enumerate(waiting):
        for b in waiting[i + 1:]:
            same = a.location_id == b.location_id
            close = geo.haversine_km(a.dropoff_lat, a.dropoff_lng, b.dropoff_lat, b.dropoff_lng) <= 1.5
            if same and close:
                out.append({"deliveries": [a.id, b.id], "why": "same restaurant, drop-offs close together"})
            elif same:
                out.append({"deliveries": [a.id, b.id], "why": "same restaurant"})
    return out[:10]
