"""Driver Assistant tools, for drivers employed by the platform. Read-only: drivers change job status with the
buttons in the app (picked up, delivered), so a misheard chat message can never close a delivery."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent.tools import ToolContext, ToolError, with_cache
from app.models import CustomerOrder, Delivery, Location
from app.money import fmt_money
from app.services import fleet

TZ = ZoneInfo("America/Vancouver")

DRIVER_TOOLS = with_cache([
    {
        "name": "get_my_deliveries",
        "description": "The driver's current jobs (to pick up, on the way) with addresses, distance, arrival "
        "estimates and a navigation link, plus batching suggestions for waiting orders.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_shift",
        "description": "Whether the driver is on shift, since when, and hours worked today.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_my_stats",
        "description": "Hours, deliveries, deliveries per hour, on-time %, km, wages, tips and earnings for a "
        "period, and progress towards the driver's weekly earnings goal.",
        "input_schema": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["today", "this_week", "last_week", "this_month"]}}},
    },
    {
        "name": "get_busy_areas",
        "description": "Where deliveries are waiting right now (by restaurant), to decide where to wait between jobs.",
        "input_schema": {"type": "object", "properties": {}},
    },
])


def period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(TZ)
    today = datetime.combine(local.date(), time(0), tzinfo=TZ)
    if period == "today":
        return today, today + timedelta(days=1)
    week = today - timedelta(days=local.weekday())
    if period == "this_week":
        return week, week + timedelta(days=7)
    if period == "last_week":
        return week - timedelta(days=7), week
    if period == "this_month":
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt
    raise ValueError("period must be today, this_week, last_week or this_month")


async def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    from app.models import utcnow

    s, driver = ctx.session, ctx.driver
    if driver is None:
        raise ToolError("This account isn't set up as a driver")
    try:
        if name == "get_my_deliveries":
            rows = list(await s.scalars(select(Delivery).where(
                Delivery.driver_id == driver.id, Delivery.status.in_(fleet.ACTIVE)).order_by(Delivery.assigned_at)))
            out = []
            for d in rows:
                out.append(fleet.delivery_out(d, await s.get(CustomerOrder, d.customer_order_id),
                                              await s.get(Location, d.location_id)))
            return {"status": driver.status, "deliveries": out,
                    "batching_suggestions": await fleet.batching_suggestions(s)}
        if name == "get_shift":
            shift = await fleet.open_shift(s, driver)
            start, end = period_bounds("today", utcnow())
            today = await fleet.stats(s, driver, start, end)
            return {"status": driver.status, "on_shift_since": shift.starts_at.astimezone(TZ).strftime("%H:%M")
                    if shift else None, "hours_today": today["hours"], "deliveries_today": today["deliveries"],
                    "hourly_wage": fmt_money(driver.hourly_wage_cents)}
        if name == "get_my_stats":
            period = args.get("period") or "this_week"
            start, end = period_bounds(period, utcnow())
            out = {"period": period, **await fleet.stats(s, driver, start, end)}
            if driver.weekly_goal_cents and period == "this_week":
                out["weekly_goal"] = fmt_money(driver.weekly_goal_cents)
                out["goal_progress_pct"] = round(out["earnings_cents"] / driver.weekly_goal_cents * 100)
            out["note"] = "Tips are 100% yours; wages are paid for every hour on shift, busy or not."
            return out
        if name == "get_busy_areas":
            return {"areas": await fleet.busy_areas(s)}
        raise ToolError(f"Unknown tool {name}")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
