"""Driver app (employed drivers, invite only) and the fleet dashboard for dispatchers and platform admins."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_driver import period_bounds
from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.models import CustomerOrder, Delivery, Driver, Location, User, utcnow
from app.money import fmt_money
from app.services import fleet

router = APIRouter(tags=["fleet"])


async def require_driver(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Driver:
    driver = await fleet.driver_for_user(session, user.id, user.email)
    if driver is None:
        raise HTTPException(403, "This account isn't one of our drivers. Ask the dispatcher for an invite.")
    return driver


async def require_dispatch(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> AuthUser:
    if user.email.lower() in get_settings().platform_admins:
        return user
    driver = await fleet.driver_for_user(session, user.id, user.email)
    if driver is not None and driver.role == "dispatcher":
        return user
    raise HTTPException(403, "Dispatcher or platform admin only")


def driver_out(d: Driver) -> dict:
    return {"id": d.id, "name": d.name, "email": d.email, "phone": d.phone, "role": d.role, "vehicle": d.vehicle,
            "status": d.status, "active": d.active, "hourly_wage": fmt_money(d.hourly_wage_cents),
            "hourly_wage_cents": d.hourly_wage_cents, "weekly_goal_cents": d.weekly_goal_cents,
            "joined": d.user_id is not None,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}


async def _jobs(session: AsyncSession, driver: Driver) -> list[dict]:
    rows = list(await session.scalars(select(Delivery).where(
        Delivery.driver_id == driver.id, Delivery.status.in_(fleet.ACTIVE)).order_by(Delivery.assigned_at)))
    out = []
    for d in rows:
        order = await session.get(CustomerOrder, d.customer_order_id)
        customer = await session.get(User, order.customer_user_id) if order else None
        out.append(fleet.delivery_out(d, order, await session.get(Location, d.location_id), customer))
    return out


# --- Driver -------------------------------------------------------------------------------------------


@router.get("/driver/me")
async def driver_me(driver: Driver = Depends(require_driver), session: AsyncSession = Depends(get_session)):
    shift = await fleet.open_shift(session, driver)
    start, end = period_bounds("today", utcnow())
    week = period_bounds("this_week", utcnow())
    week_stats = await fleet.stats(session, driver, *week)
    return {
        "driver": driver_out(driver),
        "shift": {"started_at": shift.starts_at.isoformat()} if shift else None,
        "today": await fleet.stats(session, driver, start, end),
        "this_week": week_stats,
        "goal_progress_pct": round(week_stats["earnings_cents"] / driver.weekly_goal_cents * 100)
        if driver.weekly_goal_cents else None,
        "deliveries": await _jobs(session, driver),
        "service_area": {"hub_lat": get_settings().delivery_hub_lat, "hub_lng": get_settings().delivery_hub_lng,
                         "radius_km": get_settings().delivery_radius_km},
    }


class PositionIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


@router.post("/driver/shift/start")
async def shift_start(body: PositionIn | None = None, driver: Driver = Depends(require_driver),
                      session: AsyncSession = Depends(get_session)):
    shift = await fleet.start_shift(session, driver, body.lat if body else None, body.lng if body else None)
    return {"ok": True, "started_at": shift.starts_at.isoformat(), "deliveries": await _jobs(session, driver)}


class EndShiftIn(BaseModel):
    on_call: bool = False  # stay reachable for busy alerts after the shift


@router.post("/driver/shift/end")
async def shift_end(body: EndShiftIn | None = None, driver: Driver = Depends(require_driver),
                    session: AsyncSession = Depends(get_session)):
    try:
        await fleet.end_shift(session, driver, bool(body and body.on_call))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "status": driver.status}


class AvailabilityIn(BaseModel):
    status: str = Field(pattern="^(off|on_call)$")


@router.post("/driver/availability")
async def availability(body: AvailabilityIn, driver: Driver = Depends(require_driver),
                       session: AsyncSession = Depends(get_session)):
    if driver.status == "on_shift":
        raise HTTPException(409, "End your shift first")
    driver.status = body.status
    await session.commit()
    return {"ok": True, "status": driver.status}


@router.post("/driver/location")
async def location(body: PositionIn, driver: Driver = Depends(require_driver),
                   session: AsyncSession = Depends(get_session)):
    fleet.update_position(driver, body.lat, body.lng)
    await session.commit()
    return {"ok": True}


@router.get("/driver/deliveries")
async def deliveries(driver: Driver = Depends(require_driver), session: AsyncSession = Depends(get_session)):
    return {"deliveries": await _jobs(session, driver)}


class DeliveryStatusIn(BaseModel):
    status: str = Field(pattern="^(picked_up|delivered)$")


@router.post("/driver/deliveries/{delivery_id}/status")
async def delivery_status(delivery_id: str, body: DeliveryStatusIn, driver: Driver = Depends(require_driver),
                          session: AsyncSession = Depends(get_session)):
    d = await session.get(Delivery, delivery_id)
    if d is None or d.driver_id != driver.id:
        raise HTTPException(404, "Delivery not found")
    try:
        await fleet.advance(session, driver, d, body.status)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "deliveries": await _jobs(session, driver)}


@router.get("/driver/stats")
async def driver_stats(period: str = Query("this_week", pattern="^(today|this_week|last_week|this_month)$"),
                       driver: Driver = Depends(require_driver), session: AsyncSession = Depends(get_session)):
    return {"period": period, **await fleet.stats(session, driver, *period_bounds(period, utcnow()))}


class GoalIn(BaseModel):
    weekly_goal_cents: int | None = Field(default=None, ge=0, le=1_000_000)


@router.put("/driver/goal")
async def goal(body: GoalIn, driver: Driver = Depends(require_driver), session: AsyncSession = Depends(get_session)):
    driver.weekly_goal_cents = body.weekly_goal_cents
    await session.commit()
    return {"ok": True}


# --- Fleet dashboard ------------------------------------------------------------------------------------


@router.get("/fleet/overview")
async def overview(_: AuthUser = Depends(require_dispatch), session: AsyncSession = Depends(get_session)):
    drivers = list(await session.scalars(select(Driver).where(Driver.active.is_(True)).order_by(Driver.name)))
    counts = dict((await session.execute(select(Delivery.status, func.count(Delivery.id))
                                         .where(Delivery.status.in_(("waiting", *fleet.ACTIVE)))
                                         .group_by(Delivery.status))).all())
    start, end = period_bounds("today", utcnow())
    today = []
    for d in drivers:
        st = await fleet.stats(session, d, start, end)
        today.append({**driver_out(d), "today": {k: st[k] for k in ("hours", "deliveries", "deliveries_per_hour",
                                                                   "on_time_pct")}})
    on_shift = sum(1 for d in drivers if d.status == "on_shift")
    hours = sum(t["today"]["hours"] for t in today)
    done = sum(t["today"]["deliveries"] for t in today)
    return {
        "drivers": today, "on_shift": on_shift, "on_call": sum(1 for d in drivers if d.status == "on_call"),
        "deliveries": {"waiting": counts.get("waiting", 0), "assigned": counts.get("assigned", 0),
                       "picked_up": counts.get("picked_up", 0)},
        "today": {"deliveries": done, "driver_hours": round(hours, 1),
                  "deliveries_per_driver_hour": round(done / hours, 2) if hours else None,
                  "break_even_per_hour": 3.0},
        "busy_areas": await fleet.busy_areas(session),
        "batching_suggestions": await fleet.batching_suggestions(session),
    }


class DriverIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{6,14}$")
    role: str = Field(default="driver", pattern="^(driver|dispatcher)$")
    vehicle: str = Field(default="e_bike", pattern="^(e_bike|bike|car)$")
    hourly_wage_cents: int | None = Field(default=None, ge=1825, le=10_000)  # BC minimum wage $18.25 (June 2026)


@router.get("/fleet/drivers")
async def list_drivers(_: AuthUser = Depends(require_dispatch), session: AsyncSession = Depends(get_session)):
    return [driver_out(d) for d in await session.scalars(select(Driver).order_by(Driver.name))]


@router.post("/fleet/drivers")
async def invite_driver(body: DriverIn, _: AuthUser = Depends(require_dispatch),
                        session: AsyncSession = Depends(get_session)):
    """Invite a driver by email; the account with that email becomes the driver when they sign in."""
    email = body.email.lower()
    if await session.scalar(select(Driver).where(func.lower(Driver.email) == email)):
        raise HTTPException(409, "A driver with that email already exists")
    d = Driver(email=email, name=body.name, phone=body.phone, role=body.role, vehicle=body.vehicle,
               hourly_wage_cents=body.hourly_wage_cents or get_settings().default_driver_wage_cents)
    session.add(d)
    await session.commit()
    return driver_out(d)


class DriverPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{6,14}$")
    role: str | None = Field(default=None, pattern="^(driver|dispatcher)$")
    vehicle: str | None = Field(default=None, pattern="^(e_bike|bike|car)$")
    hourly_wage_cents: int | None = Field(default=None, ge=1825, le=10_000)
    active: bool | None = None


@router.patch("/fleet/drivers/{driver_id}")
async def edit_driver(driver_id: str, body: DriverPatch, _: AuthUser = Depends(require_dispatch),
                      session: AsyncSession = Depends(get_session)):
    d = await session.get(Driver, driver_id)
    if d is None:
        raise HTTPException(404, "Driver not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    if d.active is False:
        d.status = "off"
    await session.commit()
    return driver_out(d)


class AssignIn(BaseModel):
    driver_id: str


@router.post("/fleet/deliveries/{delivery_id}/assign")
async def assign(delivery_id: str, body: AssignIn, _: AuthUser = Depends(require_dispatch),
                 session: AsyncSession = Depends(get_session)):
    d = await session.get(Delivery, delivery_id)
    driver = await session.get(Driver, body.driver_id)
    if d is None or driver is None:
        raise HTTPException(404, "Delivery or driver not found")
    try:
        result = await fleet.assign(session, d, driver)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(409, "Only deliveries that are ready and waiting can be assigned")
    await session.commit()
    return {"ok": True}


@router.post("/fleet/sweep")
async def sweep(_: AuthUser = Depends(require_dispatch), session: AsyncSession = Depends(get_session)):
    result = await fleet.sweep(session)
    await session.commit()
    return result
