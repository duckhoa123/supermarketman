"""Inventory: the stock ledger, weighted-average cost, recipes and plate costing, counts, waste and COGS.

Every change to an item's stock goes through `record`, which appends a StockMovement and keeps the item's quantity
and average cost up to date. The ledger is never edited: a mistake is fixed with a new entry. Stock may go
negative (a sale recorded before its invoice); that's flagged, not blocked.

Cost is the weighted average: a delivery mixes its price into the average by quantity; everything that leaves
(sales, waste, count differences) leaves at the average. Each movement's value_cents is the change in the item's
value (qty × average), so the movements before a moment add up to the inventory value at that moment, and
COGS for a period = opening value + received − closing value.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (DailySales, Item, ItemSalesDaily, Location, RecipeLine, StockCount, StockMovement,
                        StorageArea, utcnow)

KINDS = ("opening", "receive", "sale", "waste", "count", "adjust", "pos", "import", "revalue")
ADDED = ("opening", "receive", "revalue")  # value coming in: what COGS counts as bought
WASTE_REASONS = {
    "spoiled": "Spoiled", "expired": "Past its date", "dropped": "Dropped or spilled", "overcooked": "Overcooked",
    "returned": "Sent back", "staff_meal": "Staff meal", "comp": "Given away", "other": "Other",
}


class InventoryError(ValueError):
    pass


# --- Units ------------------------------------------------------------------------------------------------------

_MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
_VOLUME = {"ml": 1.0, "cl": 10.0, "dl": 100.0, "l": 1000.0, "tsp": 4.92892159375, "tbsp": 14.78676478125,
           "floz": 29.5735295625, "cup": 236.5882365, "pt": 473.176473, "qt": 946.352946, "gal": 3785.411784}
_COUNT = {"each": 1.0, "dozen": 12.0}
_ALIASES = {
    "gram": "g", "grams": "g", "gr": "g", "gm": "g", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "kgs": "kg", "ounce": "oz", "ounces": "oz", "lbs": "lb", "pound": "lb", "pounds": "lb", "#": "lb",
    "milligram": "mg", "milligrams": "mg",
    "millilitre": "ml", "milliliter": "ml", "millilitres": "ml", "milliliters": "ml", "mls": "ml",
    "litre": "l", "liter": "l", "litres": "l", "liters": "l", "ltr": "l", "lt": "l",
    "teaspoon": "tsp", "teaspoons": "tsp", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp",
    "fl oz": "floz", "fl. oz": "floz", "fl.oz": "floz", "fluid ounce": "floz", "fluid ounces": "floz",
    "cups": "cup", "pint": "pt", "pints": "pt", "quart": "qt", "quarts": "qt", "gallon": "gal", "gallons": "gal",
    "ea": "each", "pc": "each", "pcs": "each", "piece": "each", "pieces": "each", "unit": "each", "units": "each",
    "ct": "each", "count": "each", "": "each", "dz": "dozen", "doz": "dozen",
}
UNITS = list(_MASS) + list(_VOLUME) + list(_COUNT)


def norm_unit(unit: str | None) -> str:
    u = (unit or "").strip().lower().rstrip(".")
    return _ALIASES.get(u, u)


def _dimension(u: str) -> tuple[str, float] | None:
    for dim, table in (("mass", _MASS), ("volume", _VOLUME), ("count", _COUNT)):
        if u in table:
            return dim, table[u]
    return None


def convert(qty: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """qty in from_unit, expressed in to_unit; None when they don't convert (grams to each, say)."""
    a, b = norm_unit(from_unit), norm_unit(to_unit)
    if a == b:
        return qty
    da, db = _dimension(a), _dimension(b)
    if da is None or db is None or da[0] != db[0]:
        return None
    return qty * da[1] / db[1]


# --- The ledger ---------------------------------------------------------------------------------------------------


def _value(qty: float | None, avg: float | None) -> int:
    return round((qty or 0) * avg) if avg is not None else 0


async def _started(session: AsyncSession, item: Item) -> bool:
    return await session.scalar(select(StockMovement.id).where(StockMovement.item_id == item.id).limit(1)) is not None


def _opening(item: Item, *, at: datetime | None = None) -> StockMovement:
    if item.avg_cost is None and item.cost_cents:
        item.avg_cost = float(item.cost_cents)
    qty = item.stock_qty or 0.0
    return StockMovement(location_id=item.location_id, item_id=item.id, item_name=item.name, kind="opening",
                         qty_delta=qty, qty_after=qty, unit=item.unit, unit_cost=item.avg_cost,
                         value_cents=_value(qty, item.avg_cost), source="opening",
                         note="Stock on hand when the ledger started",
                         created_at=(at or utcnow()) - timedelta(seconds=1))


async def ensure_openings(session: AsyncSession, location: Location) -> int:
    """Items whose stock was set before the ledger existed get an opening entry, so the ledger adds up."""
    started = select(StockMovement.item_id).where(StockMovement.location_id == location.id,
                                                  StockMovement.item_id.is_not(None)).distinct()
    items = list(await session.scalars(select(Item).where(Item.location_id == location.id,
                                                          Item.stock_qty.is_not(None), Item.id.not_in(started))))
    for item in items:
        session.add(_opening(item))
    if items:
        await session.flush()
    return len(items)


async def record(session: AsyncSession, item: Item, kind: str, qty_delta: float, *, unit_cost: float | None = None,
                 source: str | None = None, reason: str | None = None, note: str | None = None,
                 user_id: str | None = None, at: datetime | None = None) -> StockMovement:
    """Change an item's stock by qty_delta (in its stock unit) and write it in the ledger.

    unit_cost (cents per stock unit) is the price paid, for stock coming in; it's mixed into the average.
    Everything else moves at the current average.
    """
    if kind not in KINDS:
        raise InventoryError(f"Unknown movement {kind}")
    if item.stock_qty is not None and not await _started(session, item):
        session.add(_opening(item, at=at))
    elif item.avg_cost is None and item.cost_cents:
        item.avg_cost = float(item.cost_cents)
    before_qty, before_avg = item.stock_qty or 0.0, item.avg_cost
    after_qty = before_qty + qty_delta
    avg = before_avg
    if qty_delta > 0 and unit_cost is not None and unit_cost >= 0:
        if before_avg is None or before_qty <= 0:
            avg = float(unit_cost)  # nothing (or less than nothing) on hand: the new price is the cost
        else:
            avg = (before_qty * before_avg + qty_delta * unit_cost) / after_qty
    moved_at = unit_cost if qty_delta > 0 and unit_cost is not None else avg
    item.stock_qty, item.avg_cost = round(after_qty, 6), avg
    if avg is not None:
        item.cost_cents = round(avg)
    row = StockMovement(location_id=item.location_id, item_id=item.id, item_name=item.name, kind=kind,
                        qty_delta=round(qty_delta, 6), qty_after=item.stock_qty, unit=item.unit, unit_cost=moved_at,
                        value_cents=_value(after_qty, avg) - _value(before_qty, before_avg), source=source,
                        reason=reason, note=note, user_id=user_id, created_at=at or utcnow())
    session.add(row)
    return row


async def set_qty(session: AsyncSession, item: Item, qty: float, kind: str = "count", **kw) -> StockMovement | None:
    """Stock is now `qty` (a count, the POS's own level, an import): the difference goes in the ledger."""
    delta = qty - (item.stock_qty or 0.0)
    if item.stock_qty is not None and abs(delta) < 1e-9:
        return None
    return await record(session, item, kind, delta, **kw)


async def revalue(session: AsyncSession, item: Item, unit_cost: float, *, user_id: str | None = None,
                  note: str | None = None) -> StockMovement:
    """The owner corrects an item's cost: the change in value is a ledger entry of its own."""
    if item.stock_qty is not None and not await _started(session, item):
        session.add(_opening(item))
    before = _value(item.stock_qty, item.avg_cost if item.avg_cost is not None else item.cost_cents)
    item.avg_cost, item.cost_cents = float(unit_cost), round(unit_cost)
    row = StockMovement(location_id=item.location_id, item_id=item.id, item_name=item.name, kind="revalue",
                        qty_delta=0, qty_after=item.stock_qty or 0.0, unit=item.unit, unit_cost=float(unit_cost),
                        value_cents=_value(item.stock_qty, item.avg_cost) - before, source="cost",
                        note=note or "Cost corrected", user_id=user_id)
    session.add(row)
    return row


def status(item: Item) -> str:
    """ok | low | out | negative | untracked: what the On hand list and par alerts show."""
    if item.stock_qty is None:
        return "untracked"
    if item.stock_qty < 0:
        return "negative"
    if item.stock_qty == 0:
        return "out"
    if item.low_stock_threshold is not None and item.stock_qty <= item.low_stock_threshold:
        return "low"
    return "ok"


# --- Recipes and plate cost ---------------------------------------------------------------------------------------


@dataclass
class CostedLine:
    line: RecipeLine
    ingredient: Item | None
    stock_qty: float | None = None  # AP amount taken from stock per plate, in the ingredient's unit
    cost_cents: float | None = None
    problem: str | None = None


@dataclass
class PlateCost:
    lines: list[CostedLine] = field(default_factory=list)
    q_factor_cents: int = 0

    @property
    def cost_cents(self) -> float:
        return sum(c.cost_cents or 0 for c in self.lines) + self.q_factor_cents

    @property
    def complete(self) -> bool:
        return bool(self.lines) and all(c.problem is None for c in self.lines)


def cost_line(line: RecipeLine, ingredient: Item | None) -> CostedLine:
    """EP portion → amount as bought (portion ÷ yield), in the ingredient's stock unit, × its average cost."""
    out = CostedLine(line=line, ingredient=ingredient)
    if ingredient is None:
        out.problem = "Ingredient was deleted"
        return out
    ep = convert(line.qty, line.unit or ingredient.unit, ingredient.unit)
    if ep is None:
        out.problem = (f"Can't turn {line.unit} into {ingredient.unit or 'each'}: use "
                       f"{ingredient.unit or 'each'} for {ingredient.name}")
        return out
    out.stock_qty = ep / (max(line.yield_pct, 1.0) / 100.0)
    cost = ingredient.avg_cost if ingredient.avg_cost is not None else ingredient.cost_cents
    if cost is None:
        out.problem = f"No cost for {ingredient.name} yet: scan an invoice or set its cost"
    else:
        out.cost_cents = out.stock_qty * cost
    return out


async def recipes(session: AsyncSession, location: Location, menu_ids: list[str] | None = None) -> dict[str, PlateCost]:
    q = select(RecipeLine).where(RecipeLine.location_id == location.id).order_by(RecipeLine.sort, RecipeLine.created_at)
    if menu_ids is not None:
        q = q.where(RecipeLine.menu_item_id.in_(menu_ids))
    lines = list(await session.scalars(q))
    ids = {ln.ingredient_id for ln in lines} | {ln.menu_item_id for ln in lines}
    items = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    out: dict[str, PlateCost] = {}
    for ln in lines:
        plate = out.setdefault(ln.menu_item_id, PlateCost(
            q_factor_cents=int((items.get(ln.menu_item_id).q_factor_cents if items.get(ln.menu_item_id) else 0) or 0)))
        plate.lines.append(cost_line(ln, items.get(ln.ingredient_id)))
    return out


def pricing(price_cents: int, plate_cents: float, target_pct: float) -> dict:
    """Food cost %, the price that hits the target food cost, and the margin per plate."""
    target = max(1.0, min(target_pct or 30.0, 95.0))
    return {
        "plate_cost_cents": round(plate_cents),
        "food_cost_pct": round(plate_cents / price_cents * 100, 1) if price_cents > 0 else None,
        "suggested_price_cents": round(plate_cents / (target / 100.0)) if plate_cents > 0 else None,
        "margin_cents": price_cents - round(plate_cents) if price_cents > 0 else None,
    }


async def save_recipe(session: AsyncSession, location: Location, menu: Item, lines: list[dict],
                      q_factor_cents: int | None) -> None:
    """Replace a menu item's recipe. lines: [{"ingredient_id", "qty", "unit", "yield_pct"}]."""
    ids = {ln["ingredient_id"] for ln in lines}
    found = {i.id: i for i in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                                       Item.id.in_(ids)))} if ids else {}
    for ln in lines:
        ing = found.get(ln["ingredient_id"])
        if ing is None:
            raise InventoryError("One of the ingredients isn't in this restaurant's inventory")
        if ing.id == menu.id:
            raise InventoryError(f"{menu.name} can't be an ingredient of itself")
        if convert(1.0, ln.get("unit") or ing.unit, ing.unit) is None:
            raise InventoryError(f"{ing.name} is counted in {ing.unit or 'each'}, so its portion can't be in "
                                 f"{ln.get('unit')}. Use {ing.unit or 'each'}, or a unit like it.")
    for old in await session.scalars(select(RecipeLine).where(RecipeLine.menu_item_id == menu.id)):
        await session.delete(old)
    for n, ln in enumerate(lines):
        session.add(RecipeLine(location_id=location.id, menu_item_id=menu.id, ingredient_id=ln["ingredient_id"],
                               qty=float(ln["qty"]), unit=(ln.get("unit") or None),
                               yield_pct=float(ln.get("yield_pct") or 100), sort=n))
    menu.q_factor_cents = q_factor_cents
    if lines and menu.recipe_since is None:
        menu.recipe_since = local_today(location)
    if not lines:
        menu.recipe_since = None
    await session.flush()


def local_today(location: Location) -> date:
    return utcnow().astimezone(ZoneInfo(location.timezone)).date()


def _day_end(day: date, tz: str) -> datetime:
    """Sales of a day go in the ledger at the end of that day (or now, for today)."""
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=ZoneInfo(tz)) - timedelta(seconds=1)
    return min(end, utcnow())


async def deplete_sales(session: AsyncSession, location: Location, days: set[date]) -> int:
    """Take ingredients out of stock for what sold on these days: units sold × recipe (EP portion ÷ yield).

    Kept in step with the day's sales: running it again after more orders, a void or a re-import adds only the
    difference, as new entries. Days before a menu item's recipe existed are left alone.
    """
    if not days:
        return 0
    plates = await recipes(session, location)
    if not plates:
        return 0
    menu = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(list(plates))))}
    by_name: dict[str, list[Item]] = {}
    for m in menu.values():
        by_name.setdefault(m.name.strip().lower(), []).append(m)
    written = 0
    for day in sorted(days):
        source = f"sales:{day.isoformat()}"
        sold = await session.execute(select(ItemSalesDaily.name, ItemSalesDaily.qty).where(
            ItemSalesDaily.location_id == location.id, ItemSalesDaily.day == day))
        want: dict[str, float] = {}
        for name, qty in sold:
            for m in by_name.get((name or "").strip().lower(), []):
                if m.recipe_since is None or day < m.recipe_since:
                    continue
                for c in plates[m.id].lines:
                    if c.ingredient is not None and c.stock_qty is not None:
                        want[c.ingredient.id] = want.get(c.ingredient.id, 0.0) + c.stock_qty * float(qty or 0)
        done = dict((await session.execute(
            select(StockMovement.item_id, func.sum(StockMovement.qty_delta)).where(
                StockMovement.location_id == location.id, StockMovement.source == source,
                StockMovement.kind == "sale").group_by(StockMovement.item_id))).all())
        ids = set(want) | {i for i in done if i}
        if not ids:
            continue
        items = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(ids)))}
        for item_id in ids:
            item = items.get(item_id)
            if item is None:
                continue
            delta = -want.get(item_id, 0.0) - float(done.get(item_id) or 0.0)
            if abs(delta) < 1e-6:
                continue
            await record(session, item, "sale", delta, source=source, at=_day_end(day, location.timezone),
                         note=None if delta < 0 or not done.get(item_id) else "Sales changed: put back")
            written += 1
    if written:
        await session.flush()
    return written


# --- Counts and waste ---------------------------------------------------------------------------------------------


async def finish_count(session: AsyncSession, location: Location, *, client_id: str, area_id: str | None,
                       lines: list[dict], user_id: str | None, counted_at: datetime | None = None) -> StockCount:
    """A finished count: each item is now what was counted; differences go in the ledger. Sent twice, kept once."""
    existing = await session.scalar(select(StockCount).where(StockCount.location_id == location.id,
                                                             StockCount.client_id == client_id))
    if existing is not None:
        return existing
    area = await session.get(StorageArea, area_id) if area_id else None
    if area is not None and area.location_id != location.id:
        raise InventoryError("That storage area isn't at this restaurant")
    ids = [ln["item_id"] for ln in lines]
    items = {i.id: i for i in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                                       Item.id.in_(ids)))} if ids else {}
    count = StockCount(location_id=location.id, storage_area_id=area.id if area else None,
                       area_name=area.name if area else "Everything", client_id=client_id, counted_by=user_id,
                       counted_at=min(counted_at or utcnow(), utcnow()))
    session.add(count)
    await session.flush()
    out, variance = [], 0
    for ln in lines:
        item = items.get(ln["item_id"])
        if item is None:
            continue
        expected = item.stock_qty
        mv = await set_qty(session, item, float(ln["qty"]), "count", source=f"count:{count.id}", user_id=user_id,
                           note=f"Counted in {count.area_name}")
        value = mv.value_cents if mv is not None else 0
        variance += value
        out.append({"item_id": item.id, "name": item.name, "unit": item.unit, "expected": expected,
                    "counted": float(ln["qty"]), "delta": mv.qty_delta if mv is not None else 0.0,
                    "value_cents": value})
    count.lines, count.variance_cents = out, variance
    await session.flush()
    return count


async def log_waste(session: AsyncSession, location: Location, item: Item, *, qty: float, unit: str | None,
                    reason: str, note: str | None, user_id: str | None, client_id: str | None = None,
                    at: datetime | None = None) -> StockMovement:
    """Throw something out: stock goes down at its average cost. A waste entry synced twice is kept once."""
    if reason not in WASTE_REASONS:
        raise InventoryError("Pick a reason for the waste")
    source = f"waste:{client_id}" if client_id else "waste"
    if client_id:
        dup = await session.scalar(select(StockMovement).where(StockMovement.location_id == location.id,
                                                               StockMovement.source == source))
        if dup is not None:
            return dup
    amount = convert(qty, unit or item.unit, item.unit)
    if amount is None:
        raise InventoryError(f"{item.name} is counted in {item.unit or 'each'}, not {unit}")
    return await record(session, item, "waste", -abs(amount), source=source, reason=reason, note=note,
                        user_id=user_id, at=min(at or utcnow(), utcnow()))


# --- COGS and food cost -------------------------------------------------------------------------------------------


async def value_at(session: AsyncSession, location: Location, when: datetime) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(StockMovement.value_cents), 0)).where(
        StockMovement.location_id == location.id, StockMovement.created_at < when)) or 0)


async def cogs(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    """Opening + received − closing for [start, end] (local days), the sales, and the actual food cost %.
    Also the variance per item: actual usage minus what the recipes say the sales used."""
    tz = ZoneInfo(location.timezone)
    t0 = datetime.combine(start, time(0), tzinfo=tz)
    t1 = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz)
    opening = await value_at(session, location, t0)
    closing = await value_at(session, location, t1)
    in_period = (StockMovement.location_id == location.id, StockMovement.created_at >= t0,
                 StockMovement.created_at < t1)
    by_kind = dict((await session.execute(select(StockMovement.kind, func.sum(StockMovement.value_cents))
                                          .where(*in_period).group_by(StockMovement.kind))).all())
    received = int(sum(by_kind.get(k) or 0 for k in ADDED))
    used = opening + received - closing
    sales = await session.execute(select(func.coalesce(func.sum(DailySales.gross_cents), 0),
                                         func.coalesce(func.sum(DailySales.tax_cents), 0),
                                         func.coalesce(func.sum(DailySales.tip_cents), 0))
                                  .where(DailySales.location_id == location.id, DailySales.day >= start,
                                         DailySales.day <= end))
    gross, tax, tips = (int(x or 0) for x in sales.one())
    net = max(0, gross - tax - tips)  # food sales: before tax and tips
    rows = (await session.execute(
        select(StockMovement.item_id, StockMovement.item_name, StockMovement.kind,
               func.sum(StockMovement.qty_delta), func.sum(StockMovement.value_cents))
        .where(*in_period).group_by(StockMovement.item_id, StockMovement.item_name, StockMovement.kind))).all()
    per: dict[str, dict] = {}
    for item_id, name, kind, qty, value in rows:
        p = per.setdefault(item_id or name, {"item_id": item_id, "name": name, "theoretical_qty": 0.0,
                                             "variance_qty": 0.0, "variance_cents": 0, "waste_cents": 0,
                                             "sales_cents": 0})
        if kind == "sale":
            p["theoretical_qty"] -= qty or 0
            p["sales_cents"] -= value or 0
        elif kind in ("waste", "count", "adjust", "pos", "import"):
            p["variance_qty"] -= qty or 0  # used beyond what the recipes account for
            p["variance_cents"] -= value or 0
            if kind == "waste":
                p["waste_cents"] -= value or 0
    units = {i.id: i.unit for i in await session.scalars(
        select(Item).where(Item.id.in_([k for k in per if k]))) } if per else {}
    variance = []
    for p in per.values():
        if abs(p["variance_qty"]) < 1e-9 and abs(p["theoretical_qty"]) < 1e-9:
            continue
        p["unit"] = units.get(p["item_id"])
        p["actual_qty"] = round(p["theoretical_qty"] + p["variance_qty"], 4)
        p["theoretical_qty"], p["variance_qty"] = round(p["theoretical_qty"], 4), round(p["variance_qty"], 4)
        variance.append(p)
    variance.sort(key=lambda p: -abs(p["variance_cents"]))
    theoretical = sum(p["sales_cents"] for p in per.values())
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "opening_cents": opening, "received_cents": received, "closing_cents": closing, "cogs_cents": used,
        "food_sales_cents": net,
        "food_cost_pct": round(used / net * 100, 1) if net > 0 else None,
        "theoretical_cents": theoretical,
        "theoretical_pct": round(theoretical / net * 100, 1) if net > 0 else None,
        "waste_cents": sum(p["waste_cents"] for p in per.values()),
        "target_pct": location.target_food_cost_pct,
        "variance": variance[:50],
    }
