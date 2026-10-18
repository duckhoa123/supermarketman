"""Customer agent tools. The customer is always the signed-in user; other customers' data is never reachable.

Ordering and booking are proposals: the agent prepares them, the customer confirms (and pays) in the app.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent.tools import ToolContext, ToolError, with_cache
from app.models import Booking, CustomerOrder, Delivery, Location
from app.services import customer, guests, ordering

FULFILMENT = {"type": "string", "enum": ["pickup", "delivery"]}

CUSTOMER_TOOLS = with_cache([
    {
        "name": "search_restaurants",
        "description": "Find restaurants in the app near the customer, best match first. Matches restaurant names, "
        "cuisines and dishes (e.g. 'ramen', 'vegan burger'). Returns distance, rating, open now, and matching dishes.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What they want, e.g. 'spicy noodles' or 'pizza'"},
            "fulfilment": {"type": "string", "enum": ["pickup", "delivery", "booking"]},
            "open_now": {"type": "boolean"},
            "radius_km": {"type": "number", "minimum": 0.5, "maximum": 20},
            "diet_tags": {"type": "array", "items": {"type": "string"},
                          "description": "e.g. vegetarian, vegan, gluten_free, halal, nut_free"},
            "max_calories": {"type": "number", "description": "Only dishes at or under this many calories"},
        }},
    },
    {
        "name": "get_menu",
        "description": "A restaurant's menu with prices, tags and nutrition per dish (when the restaurant added it), "
        "plus whether it does pickup, delivery and bookings.",
        "input_schema": {"type": "object", "properties": {"restaurant_id": {"type": "string"}},
                         "required": ["restaurant_id"]},
    },
    {
        "name": "propose_order",
        "description": "Price an order and prepare it for the customer to confirm and pay in the app. Use item ids "
        "from get_menu. Delivery goes to the customer's saved address.",
        "input_schema": {"type": "object", "properties": {
            "restaurant_id": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "item_id": {"type": "string"}, "qty": {"type": "integer", "minimum": 1, "maximum": 20}},
                "required": ["item_id"]}},
            "fulfilment": FULFILMENT,
            "notes": {"type": "string", "description": "e.g. 'no onions', 'buzz 204'"},
        }, "required": ["restaurant_id", "items", "fulfilment"]},
    },
    {
        "name": "get_my_orders",
        "description": "The customer's recent orders with status and arrival estimate, and tip suggestions for "
        "delivered orders.",
        "input_schema": {"type": "object", "properties": {"active_only": {"type": "boolean"}}},
    },
    {
        "name": "propose_booking",
        "description": "Prepare a table booking for the customer to confirm in the app. The restaurant then "
        "confirms it.",
        "input_schema": {"type": "object", "properties": {
            "restaurant_id": {"type": "string"},
            "party_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "local_time": {"type": "string", "description": "Restaurant local time 'YYYY-MM-DD HH:MM'"},
            "notes": {"type": "string"},
        }, "required": ["restaurant_id", "party_size", "local_time"]},
    },
    {
        "name": "get_my_bookings",
        "description": "The customer's upcoming and recent table bookings.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_nutrition",
        "description": "What the customer has eaten on a day (from app orders and their own log) against their "
        "daily goals: calories, protein, carbs, fat, and what's left.",
        "input_schema": {"type": "object", "properties": {
            "day": {"type": "string", "description": "today, yesterday, or YYYY-MM-DD"}}},
    },
    {
        "name": "log_food",
        "description": "Add something the customer ate (outside the app) to their nutrition log for today.",
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "calories": {"type": "number"}, "protein_g": {"type": "number"},
            "carbs_g": {"type": "number"}, "fat_g": {"type": "number"},
        }, "required": ["name"]},
    },
    {
        "name": "propose_nutrition_goals",
        "description": "Suggest daily nutrition goals for the customer to confirm. Only when they ask for goals; "
        "use what they said about their targets.",
        "input_schema": {"type": "object", "properties": {
            "calories": {"type": "number"}, "protein_g": {"type": "number"}, "carbs_g": {"type": "number"},
            "fat_g": {"type": "number"},
        }},
    },
])


async def _listed(ctx: ToolContext, restaurant_id: str) -> Location:
    loc = await ctx.session.get(Location, str(restaurant_id))
    if not await customer.visible(ctx.session, loc, ctx.user_id):
        raise ToolError("Restaurant not found. Use search_restaurants and its id.")
    return loc


def _day(value: str | None) -> date:
    today = customer.local_date()
    if not value or value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(value)


async def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    s = ctx.session
    profile = await customer.profile(s, ctx.user_id)
    home = (profile.home_lat, profile.home_lng) if profile.home_lat is not None else None
    try:
        if name == "search_restaurants":
            lat, lng = ctx.position or home or (None, None)
            result = await customer.search(
                s, viewer_id=ctx.user_id, lat=lat, lng=lng, query=args.get("query"),
                radius_km=float(args.get("radius_km") or 5),
                open_now=bool(args.get("open_now")), fulfilment=args.get("fulfilment"),
                diet_tags=args.get("diet_tags") or profile.diet_tags, max_calories=args.get("max_calories"))
            if lat is None:
                result["note"] = "No location shared or saved, so distances are unknown."
            return result
        if name == "get_menu":
            return await customer.menu(s, await _listed(ctx, args.get("restaurant_id")))
        if name == "propose_order":
            loc = await _listed(ctx, args.get("restaurant_id"))
            fulfilment = args.get("fulfilment", "pickup")
            if fulfilment == "delivery" and home is None:
                raise ToolError("No delivery address saved. Ask the customer to add one in their profile.")
            q = await ordering.quote(s, loc, args.get("items") or [], fulfilment, home if fulfilment == "delivery" else None)
            proposal = {"kind": "customer_order", "restaurant_id": loc.id, "restaurant": loc.name,
                        "fulfilment": fulfilment, "items": [{"item_id": ln["item_id"], "qty": ln["qty"]} for ln in q.lines],
                        "notes": args.get("notes"), "quote": q.out(loc.currency),
                        "label": f"Confirm {fulfilment} from {loc.name}: {q.out(loc.currency)['total']}"}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_customer_confirmation", "quote": q.out(loc.currency)}
        if name == "get_my_orders":
            stmt = select(CustomerOrder).where(CustomerOrder.customer_user_id == ctx.user_id)
            if args.get("active_only"):
                stmt = stmt.where(CustomerOrder.status.not_in(("delivered", "picked_up", "cancelled")))
            orders = list(await s.scalars(stmt.order_by(CustomerOrder.placed_at.desc()).limit(10)))
            out = []
            for o in orders:
                loc = await s.get(Location, o.location_id)
                d = await s.scalar(select(Delivery).where(Delivery.customer_order_id == o.id))
                row = ordering.order_out(o, loc.currency, restaurant=loc.name, delivery=d)
                if o.status == "delivered":
                    row["tip_suggestions"] = ordering.suggested_tips(o.subtotal_cents)
                out.append(row)
            return {"orders": out, "tips_note": "Tips go 100% to the driver, who is employed by us."}
        if name == "propose_booking":
            loc = await _listed(ctx, args.get("restaurant_id"))
            if not loc.accepts_bookings:
                raise ToolError(f"{loc.name} doesn't take bookings in the app")
            local = datetime.strptime(str(args.get("local_time")), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(loc.timezone))
            party = int(args.get("party_size") or 2)
            free = await guests.seats_free(s, loc, local, party)
            proposal = {"kind": "booking", "restaurant_id": loc.id, "restaurant": loc.name, "party_size": party,
                        "starts_at": local.isoformat(), "notes": args.get("notes"),
                        "label": f"Request a table for {party} at {loc.name}, {local.strftime('%a %d %b %H:%M')}"}
            if not free:
                return {"status": "fully_booked", "message": "That time looks full; suggest 30-60 minutes earlier or later."}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_customer_confirmation", "booking": proposal}
        if name == "get_my_bookings":
            rows = list(await s.scalars(select(Booking).where(Booking.customer_user_id == ctx.user_id)
                                        .order_by(Booking.starts_at.desc()).limit(10)))
            out = []
            for b in rows:
                loc = await s.get(Location, b.location_id)
                out.append(guests.booking_out(b, loc.timezone, loc.name))
            return {"bookings": out}
        if name == "get_nutrition":
            return await customer.nutrition_day(s, ctx.user_id, _day(args.get("day")))
        if name == "log_food":
            entry = await customer.log_food(s, ctx.user_id, customer.local_date(), str(args.get("name", "Food")), args)
            return {"status": "logged", "id": entry.id}
        if name == "propose_nutrition_goals":
            goals = {k: args.get(k) for k in ("calories", "protein_g", "carbs_g", "fat_g") if args.get(k) is not None}
            if not goals:
                raise ToolError("Give at least one goal")
            proposal = {"kind": "nutrition_goals", "goals": goals,
                        "label": "Set daily goals: " + ", ".join(f"{k.replace('_g', '')} {v:g}" for k, v in goals.items())}
            ctx.proposals.append(proposal)
            return {"status": "awaiting_customer_confirmation", "goals": goals}
        raise ToolError(f"Unknown tool {name}")
    except ordering.OrderError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
