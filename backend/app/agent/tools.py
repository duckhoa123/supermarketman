"""Tools the agent can call. Each runs a scoped query through app.services.analytics.

The Location comes from the signed-in session (ToolContext), never from the model's arguments.
"Propose" tools change nothing: they return a proposal the owner confirms in the app.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Location, Review
from app.money import PERIODS
from app.services import analytics

PERIOD_SCHEMA = {
    "type": "string",
    "description": "One of " + ", ".join(PERIODS) + ", or an explicit range 'YYYY-MM-DD..YYYY-MM-DD'.",
}

TOOLS: list[dict] = [
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
        "(yesterday vs usual), daily_digest (morning summary), and which channels to use.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["bad_review", "sales_drop", "daily_digest", "low_stock"]},
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
]

# Cache the tool definitions together with the static system prompt (tools come first in the prompt).
TOOLS[-1] = {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}


@dataclass
class ToolContext:
    session: AsyncSession
    location: Location
    user_id: str
    proposals: list[dict] = field(default_factory=list)


class ToolError(Exception):
    pass


def _limit(args: dict, default: int = 10) -> int:
    try:
        return max(1, min(50, int(args.get("limit", default))))
    except (TypeError, ValueError):
        return default


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
