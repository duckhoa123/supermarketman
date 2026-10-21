"""Cost control and period reports: labour, food cost, end-of-day / week / month summaries."""

from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, CustomerOrder, Item, ItemSalesDaily, Location, ReceivedLine, Review, StaffShift
from app.money import fmt_money, pct_change, resolve_period
from app.services import analytics
from app.services.sync import _day_bounds

REPORT_PERIODS = {"day": "yesterday", "week": "last_week", "month": "last_month"}


async def labour(session: AsyncSession, location: Location, period: str) -> dict:
    """Hours and wage cost from staff shifts, against net sales for the same period."""
    start, end = resolve_period(period, analytics.local_today(location))
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    shifts = list(await session.scalars(
        select(StaffShift).where(StaffShift.location_id == location.id, StaffShift.starts_at >= s,
                                 StaffShift.starts_at < e)
    ))
    by_role: dict[str, dict] = {}
    hours = cost = 0.0
    for sh in shifts:
        h = max(0.0, (sh.ends_at - sh.starts_at).total_seconds() / 3600)
        c = h * sh.hourly_rate_cents
        hours += h
        cost += c
        r = by_role.setdefault(sh.role or "staff", {"role": sh.role or "staff", "hours": 0.0, "cost_cents": 0})
        r["hours"] = round(r["hours"] + h, 1)
        r["cost_cents"] += round(c)
    sales = await analytics.sales_summary(session, location, period, None)
    net = sales["net_cents"]
    return {
        "period": period, "from": start.isoformat(), "to": end.isoformat(),
        "shifts": len(shifts), "hours": round(hours, 1), "cost_cents": round(cost),
        "cost": fmt_money(round(cost), location.currency),
        "net_sales": fmt_money(net, location.currency),
        "labour_pct_of_sales": round(cost / net * 100, 1) if net else None,
        "sales_per_labour_hour": fmt_money(round(net / hours), location.currency) if hours else None,
        "by_role": sorted(by_role.values(), key=lambda r: -r["cost_cents"]),
        "note": None if shifts else "No shifts recorded for this period. Add shifts in Labour to track labour cost.",
    }


async def food_cost(session: AsyncSession, location: Location, period: str) -> dict:
    """Theoretical food cost: items sold x their recipe cost (from the POS or entered in the app)."""
    start, end = resolve_period(period, analytics.local_today(location))
    sold = await session.execute(
        select(ItemSalesDaily.name, func.sum(ItemSalesDaily.qty), func.sum(ItemSalesDaily.revenue_cents))
        .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day >= start, ItemSalesDaily.day <= end)
        .group_by(ItemSalesDaily.name)
    )
    costs = {n: c for n, c in await session.execute(
        select(Item.name, Item.cost_cents).where(Item.location_id == location.id, Item.cost_cents.is_not(None)))}
    revenue = covered_revenue = cost = 0
    worst: list[dict] = []
    for name, qty, rev in sold:
        revenue += int(rev or 0)
        if name in costs:
            c = round(float(qty or 0) * costs[name])
            cost += c
            covered_revenue += int(rev or 0)
            if rev:
                worst.append({"name": name, "cost_pct": round(c / rev * 100, 1)})
    worst.sort(key=lambda r: -r["cost_pct"])
    return {
        "period": period, "cost_cents": cost, "cost": fmt_money(cost, location.currency),
        "food_cost_pct": round(cost / covered_revenue * 100, 1) if covered_revenue else None,
        "share_of_sales_with_costs_pct": round(covered_revenue / revenue * 100) if revenue else None,
        "highest_cost_items": worst[:5],
    }


async def platform_orders(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    rows = await session.execute(
        select(CustomerOrder.fulfilment, func.count(CustomerOrder.id), func.sum(CustomerOrder.subtotal_cents))
        .where(CustomerOrder.location_id == location.id, CustomerOrder.placed_at >= s, CustomerOrder.placed_at < e,
               CustomerOrder.status != "cancelled")
        .group_by(CustomerOrder.fulfilment)
    )
    out = {"pickup": 0, "delivery": 0, "food_sales_cents": 0}
    for kind, n, total in rows:
        out[kind] = int(n)
        out["food_sales_cents"] += int(total or 0)
    out["food_sales"] = fmt_money(out["food_sales_cents"], location.currency)
    return out


async def bookings_summary(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    rows = await session.execute(
        select(Booking.status, func.count(Booking.id), func.sum(Booking.party_size))
        .where(Booking.location_id == location.id, Booking.starts_at >= s, Booking.starts_at < e)
        .group_by(Booking.status)
    )
    by_status = {st: {"bookings": int(n), "covers": int(c or 0)} for st, n, c in rows}
    kept = [v for k, v in by_status.items() if k not in ("cancelled", "no_show")]
    return {"bookings": sum(v["bookings"] for v in kept), "covers": sum(v["covers"] for v in kept),
            "no_shows": by_status.get("no_show", {}).get("bookings", 0),
            "cancelled": by_status.get("cancelled", {}).get("bookings", 0)}


async def report(session: AsyncSession, location: Location, kind: str = "day") -> dict:
    """End-of-day (yesterday), weekly (last week) or monthly (last month) report with next week's forecast."""
    from app.services import forecast

    if kind not in REPORT_PERIODS:
        raise ValueError("kind must be day, week or month")
    period = REPORT_PERIODS[kind]
    compare = "same_weekday_last_week" if kind == "day" else "previous"
    sales = await analytics.sales_summary(session, location, period, compare)
    start, end = date.fromisoformat(sales["from"]), date.fromisoformat(sales["to"])
    lab = await labour(session, location, period)
    food = await food_cost(session, location, period)
    out = {
        "kind": kind, "period": period, "from": sales["from"], "to": sales["to"], "location": location.name,
        "sales": sales,
        "labour": {k: lab[k] for k in ("hours", "cost", "labour_pct_of_sales", "sales_per_labour_hour", "note")},
        "food_cost": {k: food[k] for k in ("cost", "food_cost_pct", "highest_cost_items")},
        "top_items": (await analytics.top_items(session, location, period, limit=5))["rows"],
        "slow_items": (await analytics.top_items(session, location, period, limit=3, worst=True))["rows"],
        "app_orders": await platform_orders(session, location, start, end),
        "bookings": await bookings_summary(session, location, start, end),
        "reviews": await review_summary(session, location, start, end),
        "deliveries": await deliveries_summary(session, location, start, end),
        "running_low": [i["name"] for i in (await analytics.inventory(session, location, True, limit=10))["items"]],
    }
    if kind != "day":
        days = (await analytics.sales_breakdown(session, location, period, "day"))["rows"]
        if days:
            best = max(days, key=lambda r: r["gross_cents"])
            worst = min(days, key=lambda r: r["gross_cents"])
            out["best_day"] = {"day": best["day"], "gross": fmt_money(best["gross_cents"], location.currency)}
            out["worst_day"] = {"day": worst["day"], "gross": fmt_money(worst["gross_cents"], location.currency)}
    fc = await forecast.forecast(session, location, 7)
    today = analytics.local_today(location)
    last7 = await analytics.sales_summary(  # the last 7 complete days, so a partial today doesn't skew it
        session, location, f"{(today - timedelta(days=7)).isoformat()}..{(today - timedelta(days=1)).isoformat()}", None)
    out["next_7_days"] = {
        "expected": fc["total_expected"],
        "vs_last_7_days_pct": pct_change(fc["total_expected_cents"], last7["gross_cents"]),
        "holidays": [d["holiday"] for d in fc["days"] if d.get("holiday")],
        "busiest_day": max(fc["days"], key=lambda d: d.get("expected_cents") or 0)["day"],
    }
    return out


async def deliveries_summary(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    """Spend on deliveries received (from scanned invoices) and supplier price changes in the period."""
    rows = list(await session.scalars(select(ReceivedLine).where(
        ReceivedLine.location_id == location.id, ReceivedLine.received_on >= start, ReceivedLine.received_on <= end)))
    spend = sum(r.line_total_cents for r in rows)
    changes = sorted((r for r in rows if r.price_change_pct), key=lambda r: -abs(r.price_change_pct))
    return {"invoices": len({r.scan_id for r in rows}), "spend": fmt_money(spend, location.currency),
            "spend_cents": spend,
            "price_changes": [{"description": r.description, "supplier": r.supplier_name, "change_pct": r.price_change_pct,
                               "unit_price": fmt_money(r.unit_price_cents, location.currency)} for r in changes[:5]]}


async def review_summary(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    s, e = _day_bounds(start, location.timezone)[0], _day_bounds(end, location.timezone)[1]
    n, avg, unreplied = (await session.execute(
        select(func.count(Review.id), func.avg(Review.rating),
               func.sum(case((Review.reply_text.is_(None), 1), else_=0)))
        .where(Review.location_id == location.id, Review.posted_at >= s, Review.posted_at < e)
    )).one()
    return {"count": int(n or 0), "average_rating": round(float(avg), 2) if avg is not None else None,
            "unreplied": int(unreplied or 0)}


def render(r: dict) -> tuple[str, str]:
    """Plain text for email / push: numbers first, no AI call needed."""
    label = {"day": "Yesterday", "week": "Last week", "month": "Last month"}[r["kind"]]
    s = r["sales"]
    change = (s.get("comparison") or {}).get("gross_change_pct")
    lines = [f"{label}: {s['gross']} from {s['orders']} orders (avg ticket {s['avg_ticket']})"
             + (f", {change:+.0f}% vs {'last week' if r['kind'] == 'day' else 'the period before'}"
                if change is not None else "") + "."]
    lab = r["labour"]
    if lab.get("labour_pct_of_sales") is not None:
        lines.append(f"Labour: {lab['cost']} for {lab['hours']:g} hours, {lab['labour_pct_of_sales']:g}% of net sales.")
    if r["food_cost"].get("food_cost_pct") is not None:
        lines.append(f"Food cost: about {r['food_cost']['food_cost_pct']:g}% of sales.")
    if r["top_items"]:
        lines.append("Top sellers: " + ", ".join(f"{i['name']} ({i['qty']:g})" for i in r["top_items"][:3]) + ".")
    dl = r.get("deliveries") or {}
    if dl.get("invoices"):
        lines.append(f"Deliveries: {dl['spend']} on {dl['invoices']} invoice(s).")
    if dl.get("price_changes"):
        lines.append("Supplier prices: " + ", ".join(
            f"{c['description']} {c['change_pct']:+.0f}% ({c['supplier']})" for c in dl["price_changes"][:3]) + ".")
    if r.get("running_low"):
        lines.append("Running low: " + ", ".join(r["running_low"][:6]) + ".")
    app = r["app_orders"]
    if app["pickup"] or app["delivery"]:
        lines.append(f"App orders: {app['pickup']} pickup, {app['delivery']} delivery ({app['food_sales']}).")
    b = r["bookings"]
    if b["bookings"] or b["no_shows"]:
        lines.append(f"Bookings: {b['bookings']} ({b['covers']} covers), {b['no_shows']} no-shows.")
    rv = r["reviews"]
    if rv["count"]:
        lines.append(f"Reviews: {rv['count']} new, average {rv['average_rating']}★, {rv['unreplied']} awaiting a reply.")
    if r.get("best_day"):
        lines.append(f"Best day {r['best_day']['day']} ({r['best_day']['gross']}); "
                     f"slowest {r['worst_day']['day']} ({r['worst_day']['gross']}).")
    nxt = r["next_7_days"]
    trend = f" ({nxt['vs_last_7_days_pct']:+.0f}% vs the last 7 days)" if nxt["vs_last_7_days_pct"] is not None else ""
    hol = f"; includes {', '.join(nxt['holidays'])}" if nxt["holidays"] else ""
    lines.append(f"Next 7 days: about {nxt['expected']} expected{trend}{hol}.")
    title = f"{r['location']}: {label.lower()} {s['gross']}"
    return title, "\n".join(lines)
