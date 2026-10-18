"""Customer side: find restaurants, read menus, track nutrition against personal goals."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CustomerProfile, Item, Location, NutritionEntry, Organization, OrgMember, Review, utcnow
from app.money import fmt_money
from app.services import geo

NUTRIENTS = ("calories", "protein_g", "carbs_g", "fat_g")
GOAL_FIELDS = {"calories": "calories_goal", "protein_g": "protein_goal_g", "carbs_g": "carbs_goal_g",
               "fat_g": "fat_goal_g"}


async def profile(session: AsyncSession, user_id: str) -> CustomerProfile:
    p = await session.get(CustomerProfile, user_id)
    if p is None:
        p = CustomerProfile(user_id=user_id, diet_tags=[])
        session.add(p)
        await session.flush()
    return p


def profile_out(p: CustomerProfile) -> dict:
    return {"home_address": p.home_address, "home_lat": p.home_lat, "home_lng": p.home_lng,
            "diet_tags": p.diet_tags or [],
            "goals": {k: getattr(p, f) for k, f in GOAL_FIELDS.items()}}


async def visible(session: AsyncSession, location: Location | None, user_id: str) -> bool:
    """Listed restaurants are public, except demo restaurants, which only their own team can see and order from."""
    if location is None or not location.listed:
        return False
    org = await session.get(Organization, location.org_id)
    if org is None or org.plan != "demo":
        return True
    return await session.scalar(select(OrgMember.id).where(
        OrgMember.org_id == location.org_id, OrgMember.user_id == user_id)) is not None


def _item_matches(item: Item, words: list[str], diet: list[str]) -> bool:
    text = f"{item.name} {item.category or ''} {item.description or ''} {' '.join(item.tags or [])}".lower()
    return all(w in text for w in words) and all(t in (item.tags or []) for t in diet)


async def search(session: AsyncSession, *, viewer_id: str, lat: float | None, lng: float | None,
                 query: str | None = None,
                 radius_km: float = 5.0, open_now: bool = False, fulfilment: str | None = None,
                 diet_tags: list[str] | None = None, max_calories: float | None = None, limit: int = 10) -> dict:
    """Restaurants in the app near a point, best match first. Matching looks at the restaurant's name and
    cuisine and at its menu items (name, description, tags) so "vegan ramen" finds a ramen place with a
    vegan bowl."""
    s = get_settings()
    radius_km = max(0.5, min(radius_km, s.delivery_radius_km))
    words = [w for w in (query or "").lower().split() if len(w) > 1]
    diet = [t.lower() for t in diet_tags or []]
    stmt = select(Location).where(Location.listed.is_(True))
    if fulfilment == "delivery":
        stmt = stmt.where(Location.accepts_orders.is_(True), Location.accepts_delivery.is_(True))
    elif fulfilment == "pickup":
        stmt = stmt.where(Location.accepts_orders.is_(True))
    elif fulfilment == "booking":
        stmt = stmt.where(Location.accepts_bookings.is_(True))
    results = []
    for loc in list(await session.scalars(stmt)):
        if not await visible(session, loc, viewer_id):
            continue
        km = geo.haversine_km(lat, lng, loc.lat, loc.lng) if None not in (lat, lng, loc.lat, loc.lng) else None
        if km is not None and km > radius_km:
            continue
        local = utcnow().astimezone(ZoneInfo(loc.timezone))
        is_open = geo.is_open(loc.opening_hours, local)
        if open_now and is_open is False:
            continue
        items = list(await session.scalars(select(Item).where(
            Item.location_id == loc.id, Item.active.is_(True), Item.orderable.is_(True))))
        name_hit = bool(words) and all(w in f"{loc.name} {loc.cuisine or ''}".lower() for w in words)
        fits = [i for i in items if _item_matches(i, [] if name_hit else words, diet)
                and (max_calories is None or (i.calories is not None and i.calories <= max_calories))]
        if (words or diet or max_calories) and not fits:
            continue
        rating = await session.scalar(select(func.avg(Review.rating)).where(Review.location_id == loc.id))
        score = (3 if name_hit else 0) + min(len(fits), 5) * 0.4 + (float(rating or 3.5) - 3.5) \
            - (km or 0) * 0.3 + (0.5 if is_open else 0)
        results.append((score, {
            "id": loc.id, "name": loc.name, "cuisine": loc.cuisine, "address": loc.address,
            "distance_km": round(km, 1) if km is not None else None, "open_now": is_open,
            "rating": round(float(rating), 1) if rating is not None else None,
            "pickup": loc.accepts_orders, "delivery": loc.accepts_orders and loc.accepts_delivery,
            "bookings": loc.accepts_bookings,
            "matching_items": [{"id": i.id, "name": i.name, "price": fmt_money(i.price_cents, loc.currency),
                                "calories": i.calories} for i in fits[:4]],
        }))
    results.sort(key=lambda r: -r[0])
    return {"count": len(results), "radius_km": radius_km, "restaurants": [r for _, r in results[:limit]]}


async def menu(session: AsyncSession, location: Location) -> dict:
    items = list(await session.scalars(select(Item).where(
        Item.location_id == location.id, Item.active.is_(True), Item.orderable.is_(True)
    ).order_by(Item.category, Item.name)))
    return {
        "restaurant": {"id": location.id, "name": location.name, "cuisine": location.cuisine,
                       "address": location.address, "phone": location.phone, "opening_hours": location.opening_hours,
                       "pickup": location.accepts_orders, "delivery": location.accepts_orders and location.accepts_delivery,
                       "bookings": location.accepts_bookings, "tax_rate_pct": location.tax_rate_bp / 100},
        "items": [{"id": i.id, "name": i.name, "category": i.category, "description": i.description,
                   "price": fmt_money(i.price_cents, location.currency), "price_cents": i.price_cents,
                   "sold_out": i.stock_qty is not None and i.stock_qty <= 0, "tags": i.tags or [],
                   "nutrition": {k: getattr(i, k) for k in NUTRIENTS}} for i in items],
    }


def local_date(tz: str = "America/Vancouver") -> date:
    return datetime.now(ZoneInfo(tz)).date()


async def nutrition_day(session: AsyncSession, user_id: str, day: date) -> dict:
    p = await profile(session, user_id)
    entries = list(await session.scalars(select(NutritionEntry).where(
        NutritionEntry.user_id == user_id, NutritionEntry.day == day).order_by(NutritionEntry.created_at)))
    totals = {k: round(sum(getattr(e, k) or 0 for e in entries), 1) for k in NUTRIENTS}
    goals = {k: getattr(p, f) for k, f in GOAL_FIELDS.items()}
    remaining = {k: round(goals[k] - totals[k], 1) if goals[k] is not None else None for k in NUTRIENTS}
    return {"day": day.isoformat(), "totals": totals, "goals": goals, "remaining": remaining,
            "entries": [{"id": e.id, "name": e.name, "source": e.source, **{k: getattr(e, k) for k in NUTRIENTS}}
                        for e in entries]}


async def log_food(session: AsyncSession, user_id: str, day: date, name: str, values: dict) -> NutritionEntry:
    entry = NutritionEntry(user_id=user_id, day=day, name=name[:300], source="manual",
                           **{k: values.get(k) for k in NUTRIENTS})
    session.add(entry)
    await session.commit()
    return entry
