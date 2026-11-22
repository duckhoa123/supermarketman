"""Restock alerts: when an item will run out, and how much to order, from how fast it's used.

How fast: the last 28 days of what left stock (sales through the recipes, waste, prep), per day. Nobody types it.
When the next stock can be used: the supplier's next delivery that can still be ordered, plus the days the item
takes to be ready after it arrives (thawing, portioning). On top, a safety margin for surprises: 2 days unless the
item or the restaurant says otherwise.

Two alerts:
- Runs out: at this pace the item is gone before the next delivery is ready. Sent as soon as it's true.
- Order today: on a supplier's order day, everything to order from them so it lasts until the delivery after,
  plus the margin. One alert per supplier per delivery.
Items whose supplier has no delivery days get a simpler warning when they'll last less than 1 day to order,
plus the days to be ready, plus the margin.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Location, StockMovement, Supplier, utcnow

USAGE_DAYS = 28
USAGE_KINDS = ("sale", "waste", "prep")
DEFAULT_SAFETY_DAYS = 2.0
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Delivery:
    on: date  # when it arrives
    order_by: datetime  # local time the order must be in


@dataclass
class Plan:
    usage: float  # per day
    lasts_days: float
    runs_out_on: date
    status: str  # ok | tight (into the safety margin before new stock is ready) | runs_out
    next_delivery: date | None = None
    ready_on: date | None = None  # when that delivery can be used
    order_by: datetime | None = None
    order_today: bool = False
    order_qty: float = 0.0  # to order for next_delivery, in the item's unit
    reorder_point: float | None = None  # stock that's just enough until a typical delivery is ready, plus margin


def _cutoff(text: str | None) -> time:
    try:
        h, m = (int(x) for x in (text or "").split(":"))
        return time(h, m)
    except ValueError:
        return time(23, 59)


def deliveries(now: datetime, delivery_days: list[int] | None, cutoff: str | None, lead_days: int,
               count: int = 2) -> list[Delivery]:
    """The next deliveries that can still be ordered, from now (local time): a Tuesday delivery ordered by 2 pm the
    day before is gone once Monday 2 pm has passed."""
    days = sorted({int(d) % 7 for d in delivery_days or []})
    if not days:
        return []
    out: list[Delivery] = []
    day = now.date()
    for _ in range(60):
        if day.weekday() in days:
            order_by = datetime.combine(day - timedelta(days=max(0, lead_days)), _cutoff(cutoff), tzinfo=now.tzinfo)
            if order_by > now:
                out.append(Delivery(on=day, order_by=order_by))
                if len(out) == count:
                    break
        day += timedelta(days=1)
    return out


def next_arrival(today: date, delivery_days: list[int] | None, received_today: bool = False) -> date | None:
    """The next delivery that's coming, ordered or not: today's counts until its invoice is in."""
    days = {int(d) % 7 for d in delivery_days or []}
    for k in range(0 if not received_today else 1, 8):
        if (today + timedelta(days=k)).weekday() in days:
            return today + timedelta(days=k)
    return None


def _round_up(qty: float) -> float:
    return float(math.ceil(qty)) if qty >= 5 else math.ceil(qty * 10) / 10


def plan(stock: float, usage: float, now: datetime, next_two: list[Delivery], ready_days: int = 0,
         safety_days: float = DEFAULT_SAFETY_DAYS, arriving: date | None = None) -> Plan:
    """Salmon at 49 lb on Monday morning, 17 lb a day, deliveries Tuesday and Friday (ready the next day), 2 days of
    margin: Monday and Tuesday need 34 lb (fine), but that leaves 15 lb on Wednesday, less than the 34 lb margin
    (tight). Order today for Tuesday: Wednesday to Saturday is 51 lb, plus 34 lb margin, less the 15 lb left: 70 lb."""
    today = now.date()
    lasts = max(stock, 0) / usage
    p = Plan(usage=usage, lasts_days=round(lasts, 1), runs_out_on=today + timedelta(days=math.floor(lasts)), status="ok")
    margin = usage * safety_days
    if not next_two:  # no delivery days: 1 day to order and deliver, then ready, then the margin
        need = usage * (1 + ready_days)
        p.status = "runs_out" if stock < need else "tight" if stock < need + margin else "ok"
        p.reorder_point = round(need + margin, 2)
        return p
    first = next_two[0]
    # Running out is judged against the next delivery that's coming (it may already be ordered); what to order
    # against the next one that can still be ordered. Usually the same delivery.
    coming = arriving if arriving is not None and arriving <= first.on else first.on
    p.next_delivery, p.order_by = coming, first.order_by
    p.ready_on = coming + timedelta(days=ready_days)
    need = usage * (p.ready_on - today).days
    p.status = "runs_out" if stock < need else "tight" if stock < need + margin else "ok"
    p.order_today = first.order_by.date() == today
    first_ready = first.on + timedelta(days=ready_days)
    left_when_ready = max(stock - usage * (first_ready - today).days, 0)
    cover = usage * ((next_two[1].on - first.on).days if len(next_two) > 1 else 7)  # until the delivery after
    short = cover + margin - left_when_ready
    p.order_qty = _round_up(short) if short > 0 else 0.0
    if p.order_today:
        p.next_delivery, p.order_by = first.on, first.order_by
    gap = (next_two[1].on - first.on).days if len(next_two) > 1 else 1
    p.reorder_point = round(usage * (gap + ready_days) + margin, 2)
    return p


# --- From the database ------------------------------------------------------------------------------------------


async def usage_rates(session: AsyncSession, location: Location, item_ids: list[str] | None = None) -> dict[str, float]:
    """Average a day over the last 28 days of what left stock, or since the item's first movement if newer."""
    now = utcnow()
    since = now - timedelta(days=USAGE_DAYS)
    stmt = (select(StockMovement.item_id, func.sum(StockMovement.qty_delta), func.min(StockMovement.created_at))
            .where(StockMovement.location_id == location.id, StockMovement.created_at >= since,
                   StockMovement.kind.in_(USAGE_KINDS), StockMovement.qty_delta < 0)
            .group_by(StockMovement.item_id))
    if item_ids is not None:
        stmt = stmt.where(StockMovement.item_id.in_(item_ids or [""]))
    first_seen = dict((await session.execute(
        select(StockMovement.item_id, func.min(StockMovement.created_at))
        .where(StockMovement.location_id == location.id).group_by(StockMovement.item_id))).all())
    out = {}
    for item_id, total, _ in (await session.execute(stmt)).all():
        start = first_seen.get(item_id) or since
        if start.tzinfo is None:
            start = start.replace(tzinfo=now.tzinfo)
        days = min(USAGE_DAYS, max(1.0, (now - max(start, since)).total_seconds() / 86400))
        out[item_id] = -float(total) / days
    return out


def local_now(location: Location) -> datetime:
    return utcnow().astimezone(ZoneInfo(location.timezone))


async def plans(session: AsyncSession, location: Location, items: list[Item] | None = None) -> dict[str, Plan]:
    """A plan for every stocked item that's being used."""
    from app.services.inventory import stock_items

    items = items if items is not None else await stock_items(session, location, active_only=True)
    items = [i for i in items if i.stock_qty is not None and i.active]
    rates = await usage_rates(session, location, [i.id for i in items])
    suppliers = {s.id: s for s in await session.scalars(select(Supplier).where(Supplier.location_id == location.id))}
    now = local_now(location)
    from app.models import ReceivedLine

    received_today = set(await session.scalars(select(ReceivedLine.supplier_id).where(
        ReceivedLine.location_id == location.id, ReceivedLine.received_on == now.date())))
    out = {}
    for item in items:
        usage = rates.get(item.id)
        if not usage or usage <= 0:
            continue
        sup = suppliers.get(item.supplier_id or "")
        nxt = deliveries(now, sup.delivery_days, sup.order_cutoff, sup.order_lead_days or 0) if sup else []
        safety = item.safety_days if item.safety_days is not None else (location.safety_days or DEFAULT_SAFETY_DAYS)
        arriving = next_arrival(now.date(), sup.delivery_days, sup.id in received_today) if sup else None
        out[item.id] = plan(float(item.stock_qty), usage, now, nxt, item.ready_days or 0, safety, arriving)
    return out


def _qty(n: float, unit: str | None) -> str:
    return f"{n:g}{(' ' + unit) if unit else ''}"


def _day(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%A")


def _time(t: datetime) -> str:
    return t.strftime("%-I:%M %p").replace(":00 ", " ").lower()


async def check_restock(session: AsyncSession, location: Location) -> int:
    """Send the run-out warnings and today's order reminders. Each is sent once (per item and delivery, or per
    supplier and delivery), so running this after every sync doesn't repeat them."""
    from app.services.alerts import create_alert
    from app.services.inventory import stock_items

    items = {i.id: i for i in await stock_items(session, location, active_only=True)}
    by_item = await plans(session, location, list(items.values()))
    suppliers = {s.id: s for s in await session.scalars(select(Supplier).where(Supplier.location_id == location.id))}
    today = local_now(location).date()
    created = 0
    to_order: dict[str, list[tuple[Item, Plan]]] = {}
    for item_id, p in by_item.items():
        item = items[item_id]
        rate = f"about {p.lasts_days:g} day{'s' if p.lasts_days != 1 else ''} at {_qty(round(p.usage, 1), item.unit)} a day"
        if p.status == "runs_out":
            if p.next_delivery:
                ready = "" if p.ready_on == p.next_delivery else f", ready {_day(p.ready_on, today)}"
                body = (f"{_qty(round(item.stock_qty, 2), item.unit)} left, {rate}: it runs out around "
                        f"{_day(p.runs_out_on, today)}. The next delivery that can come is "
                        f"{_day(p.next_delivery, today)}{ready}. Buy some to tide you over, or take dishes that use it "
                        f"off the menu for now.")
                key = f"runout:{item.id}:{p.ready_on.isoformat()}"
            else:
                body = (f"{_qty(round(item.stock_qty, 2), item.unit)} left, {rate}: it runs out around "
                        f"{_day(p.runs_out_on, today)}. Order now. Add the supplier's delivery days to get order "
                        f"reminders before it's this tight.")
                key = f"runout:{item.id}:{today.isoformat()}"
            alert = await create_alert(session, location, kind="runout", title=f"{item.name} is running out",
                                       body=body, dedupe_key=key,
                                       data={"item_id": item.id, "link": "/inventory?tab=onhand"})
            created += alert is not None
        elif p.status == "tight" and not p.next_delivery:
            alert = await create_alert(
                session, location, kind="restock_low", title=f"Order {item.name} soon",
                body=f"{_qty(round(item.stock_qty, 2), item.unit)} left, {rate}. That's less than the time to get "
                     f"more in, plus the safety margin.",
                dedupe_key=f"restock_low:{item.id}:{today.isoformat()}",
                data={"item_id": item.id, "link": "/inventory?tab=onhand"})
            created += alert is not None
        if p.order_today and p.order_qty > 0 and item.supplier_id in suppliers:
            to_order.setdefault(item.supplier_id, []).append((item, p))
    for supplier_id, rows in to_order.items():
        sup = suppliers[supplier_id]
        first = rows[0][1]
        lines = "; ".join(f"{i.name} {_qty(p.order_qty, i.unit)} ({_qty(round(i.stock_qty, 2), i.unit)} left)"
                          for i, p in sorted(rows, key=lambda r: r[0].name.lower()))
        alert = await create_alert(
            session, location, kind="restock_order",
            title=f"Order from {sup.name} by {_time(first.order_by)} for {_day(first.next_delivery, today)}",
            body=f"To last until the delivery after, with your safety margin: {lines}.",
            dedupe_key=f"restock_order:{supplier_id}:{first.next_delivery.isoformat()}",
            data={"supplier_id": supplier_id, "link": "/supplies?tab=orders",
                  "lines": [{"item_id": i.id, "name": i.name, "qty": p.order_qty, "unit": i.unit} for i, p in rows]})
        created += alert is not None
    await session.commit()
    return created


async def queue_check(session: AsyncSession, location: Location) -> None:
    """Check stock levels soon (in the worker), after something used stock up: waste, a count, an import."""
    from app.services.jobs import enqueue

    await enqueue(session, "restock_check", {"location_id": location.id}, dedupe_key=f"restock_check:{location.id}")


def describe_schedule(sup: Supplier) -> str | None:
    days = sorted({int(d) % 7 for d in sup.delivery_days or []})
    if not days:
        return None
    lead = sup.order_lead_days or 0
    when = "the same day" if lead == 0 else "the day before" if lead == 1 else f"{lead} days before"
    by = f" by {_time(datetime.combine(date.today(), _cutoff(sup.order_cutoff)))}" if sup.order_cutoff else ""
    return f"Delivers {', '.join(DAYS[d] for d in days)} · order{by} {when}"
