"""Restaurant Manager tools. Each runs a scoped query through the services layer.

The Location comes from the signed-in session (ToolContext), never from the model's arguments.
"Propose" tools never send, post or charge anything: they return a proposal the owner confirms in the app.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomerOrder, Driver, Item, Location, Review, Supplier, User
from app.money import PERIODS, resolve_period
from app.services import analytics, forecast, guests, marketing, purchasing, receiving, reports
from app.services.sync import _day_bounds

PERIOD_SCHEMA = {
    "type": "string",
    "description": "One of " + ", ".join(PERIODS) + ", or an explicit range 'YYYY-MM-DD..YYYY-MM-DD'.",
}

RESTAURANT_TOOLS: list[dict] = [
    {
        "name": "get_sales_summary",
        "description": "Total sales, order count and average ticket for a period, with a comparison period. "
        "Use for 'how were sales', 'how much did we take', 'compared to last week'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": PERIOD_SCHEMA,
                "compare": {
                    "type": "string",
                    "enum": ["previous", "same_weekday_last_week", "last_year", "none"],
                    "description": "previous = the equal-length period just before; "
                    "same_weekday_last_week = same days one week earlier.",
                },
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_sales_breakdown",
        "description": "Sales split by hour of day, by day, by weekday (averages), by menu category or by employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": PERIOD_SCHEMA,
                "by": {"type": "string", "enum": ["hour", "day", "weekday", "category", "employee"]},
            },
            "required": ["period", "by"],
        },
    },
    {
        "name": "get_top_items",
        "description": "Best or worst selling menu items for a period, by revenue or quantity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": PERIOD_SCHEMA,
                "sort_by": {"type": "string", "enum": ["revenue", "qty"]},
                "worst": {"type": "boolean", "description": "true to list the lowest sellers first"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_inventory",
        "description": "Current stock levels from the POS. Use low_stock_only for 'what's running low'. "
        "Items only have stock levels if stock tracking is on in the POS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "low_stock_only": {"type": "boolean"},
                "search": {"type": "string", "description": "Filter by item or category name"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "get_orders",
        "description": "List individual orders (newest first) with status, total, table, staff and items.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": PERIOD_SCHEMA,
                "status": {"type": "string", "enum": ["open", "paid", "refunded", "void"]},
                "min_total": {"type": "number", "description": "Minimum order total in currency units, e.g. 200"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_tables",
        "description": "Current status of dining tables (free, occupied, reserved, cleaning).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_reviews",
        "description": "Customer reviews (Google and other connected platforms) with rating, text, AI-tagged "
        "topics and whether they have been replied to.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": PERIOD_SCHEMA,
                "max_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "unreplied_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "propose_review_reply",
        "description": "Propose a public reply to a review. Nothing is posted until the owner approves it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "review_id": {"type": "string", "description": "The id from get_reviews"},
                "reply_text": {"type": "string"},
            },
            "required": ["review_id", "reply_text"],
        },
    },
    {
        "name": "propose_low_stock_threshold",
        "description": "Propose the stock level at which the owner is alerted for an item.",
        "input_schema": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}, "threshold": {"type": "number", "minimum": 0}},
            "required": ["item_name", "threshold"],
        },
    },
    {
        "name": "propose_alert_rule",
        "description": "Propose turning on or changing an alert: bad_review (1-3 stars), sales_drop "
        "(yesterday vs usual), daily_digest (morning end-of-day report), weekly_report (Mondays), "
        "monthly_report (the 1st), low_stock, and which channels to use.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["bad_review", "sales_drop", "daily_digest", "low_stock",
                                                     "weekly_report", "monthly_report"]},
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["push", "email", "whatsapp", "sms"]},
                },
                "threshold_pct": {"type": "number", "description": "sales_drop only: e.g. -25 for a 25% drop"},
                "enabled": {"type": "boolean"},
            },
            "required": ["kind", "channels"],
        },
    },
    {
        "name": "get_forecast",
        "description": "Expected sales for the coming days, from this restaurant's own history, the recent trend, "
        "BC holidays and special days (Lunar New Year, Mother's Day...) and the weather forecast, with a "
        "prep list of expected portions for tomorrow. Use for 'how busy will we be', staffing and prep.",
        "input_schema": {"type": "object",
                         "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 14}}},
    },
    {
        "name": "get_report",
        "description": "End-of-day (yesterday), weekly (last week) or monthly (last month) report: sales vs before, "
        "labour cost %, food cost %, best and slowest items, app orders, bookings, reviews, and the next 7 days.",
        "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["day", "week", "month"]}},
                         "required": ["kind"]},
    },
    {
        "name": "get_labour",
        "description": "Staff hours and wage cost from shifts for a period, as % of net sales and sales per labour "
        "hour, split by role.",
        "input_schema": {"type": "object", "properties": {"period": PERIOD_SCHEMA}, "required": ["period"]},
    },
    {
        "name": "get_food_cost",
        "description": "Theoretical food cost for a period (items sold x recipe cost) and the items with the "
        "highest cost %.",
        "input_schema": {"type": "object", "properties": {"period": PERIOD_SCHEMA}, "required": ["period"]},
    },
    {
        "name": "get_purchase_orders",
        "description": "Supplier orders (draft, approved, sent) and the suppliers on file.",
        "input_schema": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["draft", "approved", "sent", "received", "cancelled"]}}},
    },
    {
        "name": "propose_purchase_orders",
        "description": "Draft supplier orders for everything that is low on stock (grouped by supplier). The "
        "owner reviews and approves each one in the app before it is emailed to the supplier.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_bookings",
        "description": "Table bookings for a day (from staff, phone and the customer app) with party sizes.",
        "input_schema": {"type": "object", "properties": {
            "day": {"type": "string", "description": "today, tomorrow, or YYYY-MM-DD"}}},
    },
    {
        "name": "get_guests",
        "description": "The restaurant's guest list (people who agreed to share their details): visits, spend, "
        "last visit, marketing consent.",
        "input_schema": {"type": "object", "properties": {
            "search": {"type": "string"},
            "sort": {"type": "string", "enum": ["recent", "visits", "spend"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
    },
    {
        "name": "get_marketing_insights",
        "description": "Facts for a marketing plan: slowest days and hours, rising and falling items, special days "
        "in the next 30 days, guest-list size with consent, and what reviewers praise or complain about.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_receiving_log",
        "description": "Deliveries received, from scanned supplier invoices: date, supplier, invoice number, each "
        "product with quantity, unit price and line total, and spend totals. Filter by dates, supplier or a word.",
        "input_schema": {"type": "object", "properties": {
            "period": PERIOD_SCHEMA,
            "search": {"type": "string", "description": "Product, supplier or invoice number"}}},
    },
    {
        "name": "get_price_changes",
        "description": "Products whose supplier price changed compared with the previous delivery, newest first, "
        "with old and new unit price and % change. Use for 'what got more expensive', supplier cost creep.",
        "input_schema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 730},
            "min_change_pct": {"type": "number", "description": "Only changes at least this big, e.g. 5"}}},
    },
    {
        "name": "get_price_history",
        "description": "Every price paid for a product across deliveries, by supplier, with the change since the "
        "first delivery on record.",
        "input_schema": {"type": "object", "properties": {"product": {"type": "string"}}, "required": ["product"]},
    },
    {
        "name": "get_app_orders",
        "description": "Pickup and delivery orders placed through the customer app, with status.",
        "input_schema": {"type": "object", "properties": {
            "period": PERIOD_SCHEMA,
            "status": {"type": "string", "enum": ["placed", "accepted", "ready", "out_for_delivery", "delivered",
                                                  "picked_up", "cancelled"]}}},
    },
]


def with_cache(tools: list[dict]) -> list[dict]:
    """Mark the last tool so the tool definitions are cached with the static system prompt."""
    return [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]


TOOLS = with_cache(RESTAURANT_TOOLS)


@dataclass
class ToolContext:
    session: AsyncSession
    user_id: str
    location: Location | None = None
    agent: str = "restaurant_manager"
    user: User | None = None
    driver: Driver | None = None
    position: tuple[float, float] | None = None  # where the customer is, if the app shared it
    proposals: list[dict] = field(default_factory=list)


class ToolError(Exception):
    pass


def _limit(args: dict, default: int = 10) -> int:
    try:
        return max(1, min(50, int(args.get("limit", default))))
    except (TypeError, ValueError):
        return default


def _parse_day(value: str, loc: Location):
    today = analytics.local_today(loc)
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    from datetime import date

    return date.fromisoformat(value)


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    s, loc = ctx.session, ctx.location
    try:
        if name == "get_sales_summary":
            compare = args.get("compare", "previous")
            return await analytics.sales_summary(s, loc, args.get("period", "today"),
                                                 None if compare == "none" else compare)
        if name == "get_sales_breakdown":
            return await analytics.sales_breakdown(s, loc, args.get("period", "last_7_days"), args.get("by", "day"))
        if name == "get_top_items":
            return await analytics.top_items(
                s, loc, args.get("period", "last_7_days"), args.get("sort_by", "revenue"), _limit(args),
                bool(args.get("worst", False)),
            )
        if name == "get_inventory":
            return await analytics.inventory(s, loc, bool(args.get("low_stock_only")), args.get("search"),
                                             _limit(args, 50))
        if name == "get_orders":
            min_total = args.get("min_total")
            return await analytics.list_orders(
                s, loc, args.get("period", "today"), args.get("status"),
                round(float(min_total) * 100) if min_total is not None else None, _limit(args, 20),
            )
        if name == "get_tables":
            return await analytics.tables(s, loc)
        if name == "get_reviews":
            return await analytics.list_reviews(
                s, loc, args.get("period", "last_30_days"), args.get("max_rating"),
                bool(args.get("unreplied_only")), _limit(args, 20),
            )
        if name == "propose_review_reply":
            review = await s.get(Review, str(args.get("review_id", "")))
            if review is None or review.location_id != loc.id:
                raise ToolError("Review not found. Call get_reviews first and use its id.")
            text = str(args.get("reply_text", "")).strip()
            if not text:
                raise ToolError("reply_text is empty")
            review.suggested_reply = text
            await s.flush()
            proposal = {"kind": "review_reply", "review_id": review.id, "reply_text": text,
                        "label": f"Post reply to {review.author or 'review'} ({review.rating}★)"}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_owner_approval", "proposal": proposal}
        if name == "propose_low_stock_threshold":
            item_name = str(args.get("item_name", "")).strip()
            matches = list(
                await s.scalars(
                    select(Item).where(Item.location_id == loc.id, func.lower(Item.name).contains(item_name.lower()))
                    .limit(5)
                )
            )
            if not matches:
                raise ToolError(f"No item matching '{item_name}'. Use get_inventory to find the exact name.")
            if len(matches) > 1 and not any(m.name.lower() == item_name.lower() for m in matches):
                return {"status": "ambiguous", "candidates": [m.name for m in matches]}
            item = next((m for m in matches if m.name.lower() == item_name.lower()), matches[0])
            proposal = {"kind": "low_stock_threshold", "item_id": item.id, "item_name": item.name,
                        "threshold": float(args.get("threshold", 0)),
                        "label": f"Alert me when {item.name} is at or below {float(args.get('threshold', 0)):g}"}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_owner_approval", "proposal": proposal}
        if name == "get_forecast":
            return await forecast.forecast(s, loc, int(args.get("days") or 7))
        if name == "get_report":
            return await reports.report(s, loc, args.get("kind", "day"))
        if name == "get_labour":
            return await reports.labour(s, loc, args.get("period", "last_7_days"))
        if name == "get_food_cost":
            return await reports.food_cost(s, loc, args.get("period", "last_7_days"))
        if name == "get_purchase_orders":
            suppliers = list(await s.scalars(select(Supplier).where(Supplier.location_id == loc.id)))
            return {"orders": await purchasing.list_orders(s, loc, args.get("status")),
                    "suppliers": [{"id": x.id, "name": x.name, "has_email": bool(x.email)} for x in suppliers]}
        if name == "propose_purchase_orders":
            result = await purchasing.draft_from_low_stock(s, loc)
            await s.commit()
            names = {x.id: x.name for x in await s.scalars(select(Supplier).where(Supplier.location_id == loc.id))}
            out = []
            for po in result["drafts"]:
                proposal = {"kind": "purchase_order", "purchase_order_id": po.id,
                            "supplier": names.get(po.supplier_id), "lines": po.lines,
                            "label": f"Approve and email order to {names.get(po.supplier_id, 'supplier')} "
                            f"({len(po.lines)} item{'s' if len(po.lines) != 1 else ''})"}
                ctx.proposals.append(proposal)
                out.append(proposal)
            if not out:
                return {"status": "nothing_to_order", "low_items_without_supplier": result["no_supplier"]}
            return {"status": "awaiting_owner_approval", "drafts": out,
                    "low_items_without_supplier": result["no_supplier"]}
        if name == "get_bookings":
            day = _parse_day(args.get("day") or "today", loc)
            tz = ZoneInfo(loc.timezone)
            start = datetime.combine(day, time(0), tzinfo=tz)
            rows = await guests.list_bookings(s, loc, start, start + timedelta(days=1))
            return {"day": day.isoformat(), "bookings": rows,
                    "covers": sum(b["party_size"] for b in rows if b["status"] not in ("cancelled", "no_show"))}
        if name == "get_guests":
            return await guests.list_guests(s, loc, args.get("search"), args.get("sort", "recent"), _limit(args, 20))
        if name == "get_marketing_insights":
            return await marketing.insights(s, loc)
        if name == "get_receiving_log":
            start, end = resolve_period(args.get("period") or "last_30_days", analytics.local_today(loc))
            out = await receiving.log_lines(s, loc, start=start, end=end, search=args.get("search"), limit=60)
            by_supplier: dict[str, int] = {}
            for ln in out["lines"]:
                by_supplier[ln["supplier"] or "Unknown"] = by_supplier.get(ln["supplier"] or "Unknown", 0) \
                    + ln["line_total_cents"]
            return {"from": start.isoformat(), "to": end.isoformat(), "lines_shown": out["count"],
                    "total": out["total"], "by_supplier_cents": by_supplier,
                    "lines": [{k: ln[k] for k in ("received_on", "supplier", "invoice_number", "description", "qty",
                                                  "unit", "unit_price", "line_total", "price_change_pct")}
                              for ln in out["lines"][:60]]}
        if name == "get_price_changes":
            out = await receiving.price_changes(s, loc, int(args.get("days") or 90), float(args.get("min_change_pct") or 0))
            out["changes"] = [{k: c[k] for k in ("received_on", "supplier", "description", "unit", "unit_price",
                                                 "previous_unit_price", "price_change_pct")} for c in out["changes"][:40]]
            return out
        if name == "get_price_history":
            return await receiving.price_history(s, loc, search=str(args.get("product", "")).strip() or None)
        if name == "get_app_orders":
            start, end = resolve_period(args.get("period") or "today", analytics.local_today(loc))
            st, en = _day_bounds(start, loc.timezone)[0], _day_bounds(end, loc.timezone)[1]
            stmt = select(CustomerOrder).where(CustomerOrder.location_id == loc.id, CustomerOrder.placed_at >= st,
                                               CustomerOrder.placed_at < en)
            if args.get("status"):
                stmt = stmt.where(CustomerOrder.status == args["status"])
            from app.services.ordering import order_out

            rows = list(await s.scalars(stmt.order_by(CustomerOrder.placed_at.desc()).limit(30)))
            return {"orders": [order_out(o, loc.currency, for_restaurant=True) for o in rows]}
        if name == "propose_alert_rule":
            channels = [c for c in args.get("channels", []) if c in ("push", "email", "whatsapp", "sms")]
            proposal = {"kind": "alert_rule", "rule_kind": args.get("kind"), "channels": channels or ["push"],
                        "enabled": bool(args.get("enabled", True)),
                        "params": {"threshold_pct": args["threshold_pct"]} if "threshold_pct" in args else {},
                        "label": f"{'Turn on' if args.get('enabled', True) else 'Turn off'} "
                        f"{str(args.get('kind', '')).replace('_', ' ')} alerts via {', '.join(channels or ['push'])}"}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_owner_approval", "proposal": proposal}
        raise ToolError(f"Unknown tool {name}")
    except ValueError as exc:  # bad period string etc.
        raise ToolError(str(exc)) from exc
