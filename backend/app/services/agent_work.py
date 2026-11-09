"""What each agent is working on, and a record of what it did: the "Working on" and "Activity" screens.

Nothing here is stored separately. Every entry is read from the tables the app already keeps (supplier orders,
reviews, invoices, app orders, bookings, alerts, syncs, deliveries), so the record can't drift from what
actually happened. Each entry has one state:

- needs_you:   prepared and waiting for a person's OK (a drafted supplier order, a review to answer)
- in_progress: under way (an order being made, a delivery on the road, an invoice being read)
- scheduled:   will happen on its own (the morning brief, the next POS sync)
- done:        happened; `by_you` marks what this person approved

Entries carry a `proposal` when the app can show its usual confirm card (the same shape the chat streams), and
a `link` to the classic screen where the full detail lives.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    Booking,
    CustomerOrder,
    CustomerProfile,
    Delivery,
    Driver,
    DriverShift,
    Integration,
    InvoiceScan,
    Location,
    NutritionEntry,
    PurchaseOrder,
    Review,
    Supplier,
    utcnow,
)
from app.money import fmt_money

PERSONAL_TZ = ZoneInfo("America/Vancouver")
RECENT_DAYS = 7
RECENT_LIMIT = 40
SYNC_EVERY = timedelta(minutes=15)


@dataclass
class Entry:
    id: str
    kind: str
    state: str  # needs_you | in_progress | scheduled | done
    title: str
    detail: str = ""
    at: datetime | None = None
    source: str = ""
    link: str | None = None
    by_you: bool = False
    proposal: dict | None = None
    steps: list[dict] = field(default_factory=list)

    def out(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat() if self.at else None
        return d


def _clip(text: str | None, n: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _qty(q) -> str:
    """6.0 -> "6", 2.5 -> "2.5"."""
    if isinstance(q, float) and q.is_integer():
        return str(int(q))
    return "" if q is None else str(q)


def _lines_summary(lines: list[dict], n: int = 3) -> str:
    parts = [" ".join(p for p in (_qty(ln.get("qty")), ln.get("unit") or "", ln.get("name") or "") if p)
             for ln in lines[:n]]
    more = len(lines) - n
    return ", ".join(parts) + (f" and {more} more" if more > 0 else "")


def _when(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def _next_local(hour: int, tz: ZoneInfo, now: datetime) -> datetime:
    local = now.astimezone(tz)
    at = datetime.combine(local.date(), time(hour), tzinfo=tz)
    if at <= local:
        at += timedelta(days=1)
    return at


def _split(entries: list[Entry]) -> dict:
    out: dict[str, list[dict]] = {"needs_you": [], "in_progress": [], "scheduled": [], "done": []}
    for e in entries:
        out[e.state].append(e)
    oldest = datetime.min.replace(tzinfo=PERSONAL_TZ)
    for state, rows in out.items():
        # Needs you: ones that can be approved right here first, then newest. Under way: oldest first.
        # Scheduled: soonest first. Done: newest first.
        rows.sort(key=lambda e: e.at or oldest, reverse=state in ("done", "needs_you"))
        if state == "needs_you":
            rows.sort(key=lambda e: e.proposal is None)  # stable: keeps newest-first within each group
        out[state] = [e.out() for e in rows[:RECENT_LIMIT]]
    return out


# --- Restaurant Manager ---------------------------------------------------------------------------------------


ORDER_STEPS = ("placed", "accepted", "ready", "out_for_delivery", "delivered")
PICKUP_STEPS = ("placed", "accepted", "ready", "picked_up")
STEP_LABELS = {"placed": "Ordered", "accepted": "Restaurant accepted", "ready": "Ready",
               "out_for_delivery": "On the way", "delivered": "Delivered", "picked_up": "Picked up"}


def _order_steps(order: CustomerOrder) -> list[dict]:
    steps = ORDER_STEPS if order.fulfilment == "delivery" else PICKUP_STEPS
    at = steps.index(order.status) if order.status in steps else -1
    return [{"label": STEP_LABELS[s], "done": i < at, "current": i == at} for i, s in enumerate(steps)]


async def restaurant_work(session: AsyncSession, location: Location, user_id: str) -> dict:
    tz = ZoneInfo(location.timezone)
    now = utcnow()
    since = now - timedelta(days=RECENT_DAYS)
    money = lambda c: fmt_money(c, location.currency)  # noqa: E731
    entries: list[Entry] = []

    suppliers = {s.id: s for s in await session.scalars(select(Supplier).where(Supplier.location_id == location.id))}

    # Supplier orders: drafted (needs you), sent and waiting for delivery, and what happened this week.
    pos = await session.scalars(select(PurchaseOrder).where(
        PurchaseOrder.location_id == location.id,
        (PurchaseOrder.status.in_(("draft", "approved", "sent"))) | (PurchaseOrder.updated_at >= since)))
    for po in pos:
        name = suppliers[po.supplier_id].name if po.supplier_id in suppliers else "the supplier"
        lines = po.lines or []
        if po.status == "draft":
            entries.append(Entry(
                id=f"po:{po.id}", kind="purchase_order", state="needs_you", title=f"Order from {name}",
                detail=_lines_summary(lines), at=po.created_at, source="Supplies", link="/supplies?tab=orders",
                proposal={"kind": "purchase_order", "label": f"Order from {name}", "purchase_order_id": po.id,
                          "supplier": name,
                          "lines": [{"name": ln.get("name"), "qty": ln.get("qty"), "unit": ln.get("unit")}
                                    for ln in lines]}))
        elif po.status in ("approved", "sent") and po.send_error:
            entries.append(Entry(
                id=f"po:{po.id}", kind="purchase_order", state="needs_you", title=f"Couldn't send the order to {name}",
                detail=_clip(po.send_error), at=po.approved_at or po.updated_at, source="Supplies",
                link="/supplies?tab=orders"))
        elif po.status in ("approved", "sent"):
            due = f" · due {po.delivery_date.strftime('%a %-d %b')}" if po.delivery_date else ""
            entries.append(Entry(
                id=f"po:{po.id}", kind="purchase_order", state="in_progress", title=f"Waiting for {name}'s delivery",
                detail=f"{_lines_summary(lines)}{due}", at=po.sent_at or po.approved_at, source="Supplies",
                link="/supplies?tab=orders"))
        if po.approved_at and po.approved_at >= since:
            entries.append(Entry(
                id=f"po-approved:{po.id}", kind="purchase_order", state="done",
                title=f"Sent the order to {name}" if po.sent_at else f"Approved the order to {name}",
                detail=_lines_summary(lines) + (f" · by {po.sent_via}" if po.sent_via else ""),
                at=po.sent_at or po.approved_at, source="Supplies", link="/supplies?tab=orders",
                by_you=po.approved_by == user_id))
        if po.status == "received" and po.updated_at >= since:
            entries.append(Entry(
                id=f"po-received:{po.id}", kind="purchase_order", state="done", title=f"{name}'s delivery arrived",
                detail=_lines_summary(lines), at=po.updated_at, source="Supplies", link="/supplies?tab=orders"))

    # Reviews: low ratings with no reply yet need you; replies this week are done.
    reviews = await session.scalars(select(Review).where(
        Review.location_id == location.id,
        ((Review.reply_text.is_(None)) & (Review.rating <= 3) & (Review.posted_at >= now - timedelta(days=60)))
        | (Review.replied_at >= since)).order_by(Review.posted_at.desc()).limit(30))
    for r in reviews:
        who = r.author or "A guest"
        if r.reply_text is None:
            label = f"{who}, {r.rating} star{'s' if r.rating != 1 else ''}: “{_clip(r.text, 160)}”"
            entries.append(Entry(
                id=f"review:{r.id}", kind="review", state="needs_you",
                title=f"Answer {who}'s {r.rating}-star review", detail=_clip(r.text), at=r.posted_at,
                source=r.source.capitalize(), link="/reviews",
                proposal={"kind": "review_reply", "label": label, "review_id": r.id, "reply_text": r.suggested_reply}
                if r.suggested_reply else None))
        elif r.replied_at:
            entries.append(Entry(
                id=f"review-reply:{r.id}", kind="review", state="done", title=f"Replied to {who}'s review",
                detail=_clip(r.reply_text), at=r.replied_at, source=r.source.capitalize(), link="/reviews"))

    # Supplier invoices: being read, waiting for a person to check, and checked in.
    scans = await session.scalars(select(InvoiceScan).where(
        InvoiceScan.location_id == location.id,
        (InvoiceScan.status.in_(("processing", "review"))) | (InvoiceScan.updated_at >= since)))
    for sc in scans:
        name = sc.supplier_name or "a supplier"
        if sc.status == "processing":
            entries.append(Entry(id=f"invoice:{sc.id}", kind="invoice", state="in_progress",
                                 title=f"Reading the invoice from {name}", at=sc.created_at, source="Supplies",
                                 link="/supplies?tab=deliveries"))
        elif sc.status == "review":
            entries.append(Entry(id=f"invoice:{sc.id}", kind="invoice", state="needs_you",
                                 title=f"Check the invoice from {name}",
                                 detail=f"{len(sc.lines or [])} lines to confirm before stock is updated",
                                 at=sc.created_at, source="Supplies", link=f"/supplies?tab=deliveries&scan={sc.id}"))
        elif sc.status == "confirmed":
            total = f" · {money(sc.total_cents)}" if sc.total_cents is not None else ""
            entries.append(Entry(id=f"invoice:{sc.id}", kind="invoice", state="done",
                                 title=f"Checked in the invoice from {name}",
                                 detail=f"{len(sc.lines or [])} lines{total}", at=sc.updated_at,
                                 source="Supplies", link=f"/supplies?tab=deliveries&scan={sc.id}",
                                 by_you=sc.confirmed_by == user_id))

    # Orders from the customer app.
    orders = await session.scalars(select(CustomerOrder).where(
        CustomerOrder.location_id == location.id,
        CustomerOrder.status.in_(("placed", "accepted", "ready", "out_for_delivery"))).order_by(CustomerOrder.placed_at))
    for o in orders:
        items = sum(int(ln.get("qty", 1)) for ln in o.lines or [])
        detail = f"{items} item{'s' if items != 1 else ''} · {money(o.total_cents)} · {o.fulfilment}"
        if o.status == "placed":
            entries.append(Entry(id=f"app-order:{o.id}", kind="app_order", state="needs_you",
                                 title="Accept a new app order", detail=detail, at=o.placed_at, source="App orders",
                                 link="/app-orders"))
        else:
            entries.append(Entry(id=f"app-order:{o.id}", kind="app_order", state="in_progress",
                                 title=f"App order: {STEP_LABELS.get(o.status, o.status).lower()}", detail=detail,
                                 at=o.placed_at, source="App orders", link="/app-orders", steps=_order_steps(o)))

    # Booking requests from the customer app or the website.
    bookings = await session.scalars(select(Booking).where(
        Booking.location_id == location.id, Booking.status == "requested", Booking.starts_at >= now))
    for b in bookings:
        entries.append(Entry(id=f"booking:{b.id}", kind="booking", state="needs_you",
                             title=f"Booking request from {b.name}",
                             detail=f"{b.party_size} people · {_when(b.starts_at, tz)}", at=b.created_at,
                             source="Bookings", link="/bookings"))

    # What the app does on its own: syncs and the morning brief.
    integrations = await session.scalars(select(Integration).where(
        Integration.location_id == location.id, Integration.status != "disconnected"))
    for integ in integrations:
        label = {"clover": "Clover", "square": "Square", "google": "Google reviews"}.get(integ.provider,
                                                                                      integ.provider.capitalize())
        what = "reviews" if integ.provider == "google" else "sales and stock"
        if integ.status == "error":
            entries.append(Entry(id=f"sync:{integ.id}", kind="sync", state="needs_you",
                                 title=f"{label} needs reconnecting", detail=_clip(integ.last_error),
                                 at=integ.updated_at, source=label, link="/settings"))
            continue
        nxt = (integ.last_synced_at or now) + SYNC_EVERY
        entries.append(Entry(id=f"sync-next:{integ.id}", kind="sync", state="scheduled",
                             title=f"Check {label} for new {what}", detail="Every 15 minutes",
                             at=max(nxt, now), source=label, link="/settings"))
        if integ.last_synced_at and integ.last_synced_at >= since:
            entries.append(Entry(id=f"sync:{integ.id}", kind="sync", state="done", title=f"Synced {what} from {label}",
                                 detail="Read only", at=integ.last_synced_at, source=label, link="/settings"))
    entries.append(Entry(id="digest-next", kind="digest", state="scheduled", title="Send your morning brief",
                         detail="Yesterday's numbers, today's bookings and anything that needs you",
                         at=_next_local(location.digest_hour, tz, now), source="Alerts", link="/alerts"))

    # Everything the team was told this week.
    alerts = await session.scalars(select(Alert).where(
        Alert.location_id == location.id, Alert.created_at >= since).order_by(Alert.created_at.desc()).limit(20))
    for a in alerts:
        entries.append(Entry(id=f"alert:{a.id}", kind="alert", state="done", title=_clip(a.title, 90),
                             detail=_clip(a.body), at=a.created_at, source="Alerts", link="/alerts"))

    return _split(entries)


# --- Customer agent --------------------------------------------------------------------------------------------


async def customer_work(session: AsyncSession, user_id: str) -> dict:
    now = utcnow()
    since = now - timedelta(days=14)
    entries: list[Entry] = []
    names: dict[str, str] = {}

    async def place(location_id: str) -> str:
        if location_id not in names:
            loc = await session.get(Location, location_id)
            names[location_id] = loc.name if loc else "the restaurant"
        return names[location_id]

    orders = await session.scalars(select(CustomerOrder).where(
        CustomerOrder.customer_user_id == user_id,
        (CustomerOrder.status.in_(("placed", "accepted", "ready", "out_for_delivery")))
        | (CustomerOrder.placed_at >= since)).order_by(CustomerOrder.placed_at.desc()).limit(40))
    for o in orders:
        where = await place(o.location_id)
        items = sum(int(ln.get("qty", 1)) for ln in o.lines or [])
        detail = f"{items} item{'s' if items != 1 else ''} · {fmt_money(o.total_cents)}"
        if o.status in ("placed", "accepted", "ready", "out_for_delivery"):
            entries.append(Entry(id=f"order:{o.id}", kind="order", state="in_progress",
                                 title=f"Your {where} order", detail=f"{detail} · {o.fulfilment}", at=o.placed_at,
                                 source=where, link="/orders", steps=_order_steps(o)))
        else:
            verb = {"delivered": "Delivered", "picked_up": "Picked up", "cancelled": "Cancelled"}.get(o.status, "Done")
            tip = f" · {fmt_money(o.tip_cents)} tip" if o.tip_cents else ""
            entries.append(Entry(id=f"order:{o.id}", kind="order", state="done", title=f"{verb}: {where} order",
                                 detail=detail + tip, at=o.completed_at or o.placed_at, source=where,
                                 link="/orders", by_you=True))

    bookings = await session.scalars(select(Booking).where(
        Booking.customer_user_id == user_id,
        (Booking.starts_at >= now) | (Booking.updated_at >= since)).order_by(Booking.starts_at))
    for b in bookings:
        where = await place(b.location_id)
        detail = f"{b.party_size} people · {_when(b.starts_at, PERSONAL_TZ)}"
        if b.status == "requested" and b.starts_at >= now:
            entries.append(Entry(id=f"booking:{b.id}", kind="booking", state="in_progress",
                                 title=f"Table at {where}: waiting for them to confirm", detail=detail,
                                 at=b.created_at, source=where, link="/orders"))
        elif b.status == "confirmed" and b.starts_at >= now:
            entries.append(Entry(id=f"booking:{b.id}", kind="booking", state="scheduled", title=f"Table at {where}",
                                 detail=f"Confirmed · {detail}", at=b.starts_at, source=where, link="/orders"))
        elif b.updated_at >= since:
            entries.append(Entry(id=f"booking:{b.id}", kind="booking", state="done",
                                 title=f"Booking at {where}: {b.status.replace('_', ' ')}", detail=detail,
                                 at=b.updated_at, source=where, link="/orders"))

    logged = await session.scalars(select(NutritionEntry).where(
        NutritionEntry.user_id == user_id, NutritionEntry.created_at >= now - timedelta(days=RECENT_DAYS),
        NutritionEntry.source == "order"))
    for n in logged:
        kcal = f"{round(n.calories)} kcal" if n.calories is not None else ""
        entries.append(Entry(id=f"food:{n.id}", kind="food_log", state="done", title=f"Logged {n.name}",
                             detail=kcal, at=n.created_at, source="Food log", link="/nutrition"))

    out = _split(entries)
    out["goal"] = await _protein_goal(session, user_id, now)
    return out


async def _protein_goal(session: AsyncSession, user_id: str, now: datetime) -> dict | None:
    profile = await session.get(CustomerProfile, user_id)
    if profile is None or not profile.protein_goal_g:
        return None
    today = now.astimezone(PERSONAL_TZ).date()
    monday = today - timedelta(days=today.weekday())
    rows = await session.scalars(select(NutritionEntry).where(
        NutritionEntry.user_id == user_id, NutritionEntry.day >= monday, NutritionEntry.day <= today))
    by_day: dict = {}
    for r in rows:
        by_day[r.day] = by_day.get(r.day, 0.0) + (r.protein_g or 0.0)
    target = profile.protein_goal_g
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        value = round(by_day.get(d, 0.0))
        days.append({"day": d.strftime("%a")[0], "value": value, "met": value >= target, "today": d == today,
                     "future": d > today})
    return {"title": f"{round(target)} g protein a day", "target": round(target), "unit": "g",
            "met_days": sum(1 for x in days if x["met"]), "days": days}


# --- Driver Assistant ------------------------------------------------------------------------------------------


async def driver_work(session: AsyncSession, driver: Driver) -> dict:
    from app.services import fleet

    now = utcnow()
    since = now - timedelta(days=RECENT_DAYS)
    entries: list[Entry] = []

    shift = await fleet.open_shift(session, driver)
    if shift:
        hours = (now - shift.starts_at).total_seconds() / 3600
        entries.append(Entry(id=f"shift:{shift.id}", kind="shift", state="in_progress",
                             title=f"On shift since {shift.starts_at.astimezone(PERSONAL_TZ).strftime('%-I:%M %p').lower()}",
                             detail=f"{hours:.1f} h so far", at=shift.starts_at, source="Shift", link="/"))
    ended = await session.scalars(select(DriverShift).where(
        DriverShift.driver_id == driver.id, DriverShift.ends_at >= since))
    for sh in ended:
        hours = (sh.ends_at - sh.starts_at).total_seconds() / 3600
        entries.append(Entry(id=f"shift:{sh.id}", kind="shift", state="done", title="Shift ended",
                             detail=f"{hours:.1f} h", at=sh.ends_at, source="Shift", link="/earnings"))

    jobs = await session.scalars(select(Delivery).where(
        Delivery.driver_id == driver.id,
        (Delivery.status.in_(fleet.ACTIVE)) | (Delivery.delivered_at >= since)))
    for d in jobs:
        loc = await session.get(Location, d.location_id)
        where = loc.name if loc else "the restaurant"
        if d.status in fleet.ACTIVE:
            title = f"Pick up at {where}" if d.status == "assigned" else f"Drop off from {where}"
            eta = f" · ETA {d.eta_at.astimezone(PERSONAL_TZ).strftime('%-I:%M %p').lower()}" if d.eta_at else ""
            entries.append(Entry(id=f"delivery:{d.id}", kind="delivery", state="in_progress", title=title,
                                 detail=f"{d.distance_km:.1f} km{eta}", at=d.assigned_at, source=where, link="/",
                                 steps=[{"label": "Assigned", "done": True, "current": False},
                                        {"label": "Picked up", "done": d.status == "picked_up",
                                         "current": d.status == "assigned"},
                                        {"label": "Delivered", "done": False, "current": d.status == "picked_up"}]))
        elif d.status == "delivered" and d.delivered_at:
            tip = f" · {fmt_money(d.tip_cents)} tip" if d.tip_cents else ""
            on_time = d.promised_at and d.delivered_at <= d.promised_at + timedelta(minutes=5)
            entries.append(Entry(id=f"delivery:{d.id}", kind="delivery", state="done", title=f"Delivered from {where}",
                                 detail=f"{d.distance_km:.1f} km{tip}{' · on time' if on_time else ''}",
                                 at=d.delivered_at, source=where, link="/earnings", by_you=True))

    out = _split(entries)
    local = now.astimezone(PERSONAL_TZ)
    week_start = datetime.combine(local.date() - timedelta(days=local.weekday()), time(0), tzinfo=PERSONAL_TZ)
    week = await fleet.stats(session, driver, week_start, week_start + timedelta(days=7))
    goal = driver.weekly_goal_cents
    out["goal"] = {
        "title": "Weekly earnings goal" if goal else "This week",
        "earned": week["earnings"], "earned_cents": week["earnings_cents"],
        "target": fmt_money(goal) if goal else None, "target_cents": goal,
        "pct": min(100, round(week["earnings_cents"] / goal * 100)) if goal else None,
        "hours": week["hours"], "deliveries": week["deliveries"], "on_time_pct": week["on_time_pct"],
    }
    return out
