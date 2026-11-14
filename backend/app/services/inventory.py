"""Inventory: the stock ledger, item cost, recipes and plate costing, counts, waste and COGS.

Every change to an item's stock goes through `record`, which appends a StockMovement and keeps the item's quantity
and cost up to date. The ledger is never edited: a mistake is fixed with a new entry. Stock may go negative (a sale
recorded before its invoice); that's flagged, not blocked.

Cost is the weighted average by default: a delivery mixes its price into the average by quantity; everything that
leaves (sales, waste, count differences) leaves at the average. A restaurant can choose the latest price instead.
Each movement's value_cents is the change in the item's value (qty × cost), so the movements before a moment add
up to the inventory value at that moment, and COGS for a period = opening value + received − closing value.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (DailySales, InventoryAudit, Item, ItemSalesDaily, Location, PlateCostHistory, RecipeLine,
                        StockCount, StockMovement, StorageArea, utcnow)

KINDS = ("opening", "receive", "sale", "waste", "count", "adjust", "pos", "import", "revalue", "prep")
ADDED = ("opening", "receive", "revalue")  # value coming in: what COGS counts as bought
USAGE = ("waste", "count", "adjust", "pos", "import")  # beyond what the recipes account for
WASTE_REASONS = {
    "spoiled": "Spoiled", "expired": "Past its date", "dropped": "Dropped or spilled", "overcooked": "Burnt or overcooked",
    "returned": "Sent back", "staff_meal": "Staff meal", "comp": "Comped", "other": "Other",
}
CATEGORIES = ["Produce", "Protein", "Dairy", "Dry goods", "Bakery", "Prep", "Beverage", "Alcohol", "Packaging",
              "Cleaning", "Supplies"]
DRINK_WORDS = ("drink", "beverage", "bar", "beer", "wine", "cocktail", "coffee", "tea", "juice", "soda", "spirit",
               "liquor", "alcohol")
BIG_GAP_CENTS, BIG_GAP_PCT = 2000, 20.0  # a count difference worth a second look


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
    "cases": "case", "cs": "case", "bottles": "bottle", "btl": "bottle", "cans": "can", "bags": "bag",
    "boxes": "box", "bx": "box", "tubs": "tub", "jars": "jar", "packs": "pack", "pk": "pack", "bunches": "bunch",
}
UNITS = list(_MASS) + list(_VOLUME) + list(_COUNT) + ["case", "bottle", "can", "bag", "box", "pack", "tub", "bunch"]


def norm_unit(unit: str | None) -> str:
    u = (unit or "").strip().lower().rstrip(".")
    return _ALIASES.get(u, u)


def _dimension(u: str) -> tuple[str, float] | None:
    for dim, table in (("mass", _MASS), ("volume", _VOLUME), ("count", _COUNT)):
        if u in table:
            return dim, table[u]
    return None


def _basic(qty: float, a: str, b: str) -> float | None:
    if a == b:
        return qty
    da, db = _dimension(a), _dimension(b)
    if da is None or db is None or da[0] != db[0]:
        return None
    return qty * da[1] / db[1]


def convert(qty: float, from_unit: str | None, to_unit: str | None,
            conversions: list[dict] | None = None) -> float | None:
    """qty in from_unit, expressed in to_unit; None when they don't convert.

    Units of the same kind convert by themselves (oz → lb, tbsp → ml). Across kinds an item's own conversions
    bridge them: "1 case = 40 lb" (purchase → count unit), "1 cup = 4.25 oz" (a density), "1 each = 180 g".
    """
    a, b = norm_unit(from_unit), norm_unit(to_unit)
    direct = _basic(qty, a, b)
    if direct is not None:
        return direct
    for c in conversions or []:
        try:
            ua, qa, ub, qb = norm_unit(c.get("unit")), float(c.get("qty") or 1), norm_unit(c.get("to_unit")), \
                float(c.get("to_qty") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if qa <= 0 or qb <= 0:
            continue
        for x, qx, y, qy in ((ua, qa, ub, qb), (ub, qb, ua, qa)):
            here = _basic(qty, a, x)
            if here is not None:
                there = _basic(here / qx * qy, y, b)
                if there is not None:
                    return there
    return None


def item_convert(qty: float, from_unit: str | None, item: Item) -> float | None:
    """An amount in some unit, in the item's count unit."""
    return convert(qty, from_unit or item.unit, item.unit, item.conversions)


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
    """Change an item's stock by qty_delta (in its count unit) and write it in the ledger.

    unit_cost (cents per count unit) is the price paid, for stock coming in: mixed into the average, or (with the
    "latest price" method) replacing it. Everything else moves at the item's current cost.
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
        location = await session.get(Location, item.location_id)
        if before_avg is None or before_qty <= 0 or (location is not None and location.cost_method == "last"):
            avg = float(unit_cost)  # nothing (or less than nothing) on hand, or latest price: the new price
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
    """Stock is now `qty` (the POS's own level, an import, a correction): the difference goes in the ledger."""
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
    """The par levels: ok | approaching | at_par | below_par | out | negative | untracked.

    low_stock_threshold is the reorder point; par_level is what you want on hand after a delivery.
    """
    q = item.stock_qty
    if q is None:
        return "untracked"
    if q < 0:
        return "negative"
    if q == 0:
        return "out"
    rp, par = item.low_stock_threshold, item.par_level
    if rp is not None and q <= rp:
        return "below_par"
    if par is not None and q <= par:
        return "at_par"
    if (par is not None and q <= par * 1.25) or (par is None and rp is not None and q <= rp * 1.25):
        return "approaching"
    return "ok"


def audit(session: AsyncSession, item: Item, field_name: str, old, new, user_id: str | None) -> None:
    """Recipe, par, yield and unit changes: who, when, old and new value."""
    def text(v):
        if v is None:
            return None
        return f"{v:g}" if isinstance(v, float) else str(v)

    if text(old) == text(new):
        return
    session.add(InventoryAudit(location_id=item.location_id, item_id=item.id, item_name=item.name, field=field_name,
                               old=(text(old) or "")[:2000] or None, new=(text(new) or "")[:2000] or None,
                               user_id=user_id))


# --- Recipes, prep items and plate cost ----------------------------------------------------------------------------

MAX_DEPTH = 4  # a dish → a sauce → its base → a stock


@dataclass
class CostedLine:
    line: RecipeLine
    ingredient: Item | None
    stock_qty: float | None = None  # amount as bought (EP ÷ yield) per plate or batch, in the ingredient's unit
    unit_cost: float | None = None  # cents per ingredient unit (a prep item's comes from its own recipe)
    cost_cents: float | None = None
    problem: str | None = None


@dataclass
class PlateCost:
    """One plate of a menu item, or one batch of a prep item."""

    lines: list[CostedLine] = field(default_factory=list)
    q_factor_cents: int = 0
    q_factor_pct: float = 0.0

    @property
    def ingredients_cents(self) -> float:
        return sum(c.cost_cents or 0 for c in self.lines)

    @property
    def q_cents(self) -> float:
        return self.q_factor_cents + self.ingredients_cents * (self.q_factor_pct or 0) / 100.0

    @property
    def cost_cents(self) -> float:
        return self.ingredients_cents + self.q_cents

    @property
    def complete(self) -> bool:
        return bool(self.lines) and all(c.problem is None for c in self.lines)


def is_prep(item: Item) -> bool:
    return bool(item.batch_qty)


def is_drink(item: Item) -> bool:
    cat = (item.category or "").lower()
    return any(w in cat for w in DRINK_WORDS)


def target_for(item: Item, location: Location) -> float:
    if item.target_food_cost_pct:
        return item.target_food_cost_pct
    return location.target_beverage_cost_pct if is_drink(item) else location.target_food_cost_pct


class Costing:
    """Every recipe at a restaurant, costed from the ingredients' current cost, through prep items."""

    def __init__(self, lines: list[RecipeLine], items: dict[str, Item]):
        self.items = items
        self.lines: dict[str, list[RecipeLine]] = {}
        for ln in lines:
            self.lines.setdefault(ln.menu_item_id, []).append(ln)
        self._unit: dict[str, tuple[float | None, str | None]] = {}

    def has_recipe(self, item_id: str) -> bool:
        return bool(self.lines.get(item_id))

    def unit_cost(self, item: Item, seen: frozenset = frozenset()) -> tuple[float | None, str | None]:
        """Cents per count unit, and what's wrong if it can't be worked out. A prep item costs its batch ÷ yield."""
        if item.id in self._unit:
            return self._unit[item.id]
        if is_prep(item) and self.has_recipe(item.id):
            if item.id in seen:
                return None, f"{item.name} uses itself"
            if len(seen) >= MAX_DEPTH:
                return None, "Too many levels of prep items"
            batch = self.plate(item.id, seen | {item.id})
            out = (batch.cost_cents / item.batch_qty if batch.lines else None,
                   None if batch.complete else next((c.problem for c in batch.lines if c.problem), None))
        else:
            cost = item.avg_cost if item.avg_cost is not None else (float(item.cost_cents) if item.cost_cents else None)
            out = (cost, None if cost is not None else f"No cost for {item.name} yet: scan an invoice or set its cost")
        if not seen:
            self._unit[item.id] = out
        return out

    def cost_line(self, line: RecipeLine, seen: frozenset = frozenset()) -> CostedLine:
        ing = self.items.get(line.ingredient_id)
        out = CostedLine(line=line, ingredient=ing)
        if ing is None:
            out.problem = "Ingredient was deleted"
            return out
        ep = item_convert(line.qty, line.unit, ing)
        if ep is None:
            out.problem = (f"Can't turn {line.unit} into {ing.unit or 'each'} for {ing.name}: add a conversion "
                           f"(like 1 {line.unit} = … {ing.unit or 'each'}) or use {ing.unit or 'each'}")
            return out
        out.stock_qty = ep / (max(line.yield_pct or 100.0, 1.0) / 100.0)
        out.unit_cost, problem = self.unit_cost(ing, seen)
        if out.unit_cost is not None:
            out.cost_cents = out.stock_qty * out.unit_cost
        out.problem = problem
        return out

    def plate(self, item_id: str, seen: frozenset = frozenset()) -> PlateCost:
        item = self.items.get(item_id)
        plate = PlateCost(q_factor_cents=int((item.q_factor_cents if item else 0) or 0),
                          q_factor_pct=float((item.q_factor_pct if item else 0) or 0))
        for ln in self.lines.get(item_id, []):
            plate.lines.append(self.cost_line(ln, seen | {item_id}))
        return plate

    def explode(self, item_id: str, n: float, seen: frozenset = frozenset()) -> dict[str, float]:
        """What n plates (or batches) take out of stock: raw ingredients, through prep items that aren't kept as
        stock of their own. In each ingredient's count unit."""
        out: dict[str, float] = {}
        if item_id in seen or len(seen) > MAX_DEPTH:
            return out
        for ln in self.lines.get(item_id, []):
            c = self.cost_line(ln)
            ing = c.ingredient
            if ing is None or c.stock_qty is None:
                continue
            amount = c.stock_qty * n
            if is_prep(ing) and ing.stock_qty is None and self.has_recipe(ing.id):
                for k, v in self.explode(ing.id, amount / ing.batch_qty, seen | {item_id}).items():
                    out[k] = out.get(k, 0.0) + v
            else:
                out[ing.id] = out.get(ing.id, 0.0) + amount
        return out


async def load_costing(session: AsyncSession, location: Location) -> Costing:
    lines = list(await session.scalars(select(RecipeLine).where(RecipeLine.location_id == location.id)
                                       .order_by(RecipeLine.sort, RecipeLine.created_at)))
    ids = {ln.ingredient_id for ln in lines} | {ln.menu_item_id for ln in lines}
    items = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    return Costing(lines, items)


def cost_line(line: RecipeLine, ingredient: Item | None) -> CostedLine:
    """One recipe line on its own: EP portion → amount as bought (÷ yield), × the ingredient's cost."""
    if ingredient is None:
        return Costing([], {}).cost_line(line)
    key = ingredient.id or "_"
    return Costing([], {key: ingredient}).cost_line(
        RecipeLine(ingredient_id=key, qty=line.qty, unit=line.unit, yield_pct=line.yield_pct))


def pricing(price_cents: int, plate_cents: float, target_pct: float) -> dict:
    """Food cost %, the price that hits the target food cost, and the margin per plate."""
    target = max(1.0, min(target_pct or 30.0, 95.0))
    return {
        "plate_cost_cents": round(plate_cents),
        "food_cost_pct": round(plate_cents / price_cents * 100, 1) if price_cents > 0 else None,
        "suggested_price_cents": round(plate_cents / (target / 100.0)) if plate_cents > 0 else None,
        "margin_cents": price_cents - round(plate_cents) if price_cents > 0 else None,
        "target_pct": target,
        "over_target": bool(price_cents > 0 and plate_cents / price_cents * 100 > target + 0.05),
    }


def _describe(lines: list[RecipeLine], items: dict[str, Item]) -> str:
    return "; ".join(f"{ln.qty:g} {ln.unit or ''} {items[ln.ingredient_id].name if ln.ingredient_id in items else '?'}"
                     f"{f' @ {ln.yield_pct:g}%' if (ln.yield_pct or 100) != 100 else ''}".replace("  ", " ")
                     for ln in lines) or "(none)"


async def save_recipe(session: AsyncSession, location: Location, menu: Item, lines: list[dict],
                      q_factor_cents: int | None, q_factor_pct: float | None = None, *,
                      user_id: str | None = None) -> None:
    """Replace the recipe of a menu item (one plate) or a prep item (one batch).
    lines: [{"ingredient_id", "qty", "unit", "yield_pct"}]."""
    costing = await load_costing(session, location)
    ids = {ln["ingredient_id"] for ln in lines}
    found = {i.id: i for i in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                                       Item.id.in_(ids)))} if ids else {}
    for ln in lines:
        ing = found.get(ln["ingredient_id"])
        if ing is None:
            raise InventoryError("One of the ingredients isn't in this restaurant's inventory")
        if ing.id == menu.id:
            raise InventoryError(f"{menu.name} can't be an ingredient of itself")
        if item_convert(1.0, ln.get("unit"), ing) is None:
            raise InventoryError(f"{ing.name} is counted in {ing.unit or 'each'}, so its portion can't be in "
                                 f"{ln.get('unit')}. Use {ing.unit or 'each'}, or add a conversion to {ing.name}.")
        if _uses(costing, ing.id, menu.id):
            raise InventoryError(f"{ing.name} already uses {menu.name}, so it can't go in {menu.name}'s recipe")
    old = list(await session.scalars(select(RecipeLine).where(RecipeLine.menu_item_id == menu.id)
                                     .order_by(RecipeLine.sort)))
    before = _describe(old, {**costing.items, **found})
    for o in old:
        await session.delete(o)
    new = [RecipeLine(location_id=location.id, menu_item_id=menu.id, ingredient_id=ln["ingredient_id"],
                      qty=float(ln["qty"]), unit=(ln.get("unit") or None),
                      yield_pct=float(ln.get("yield_pct") or 100), sort=n) for n, ln in enumerate(lines)]
    session.add_all(new)
    audit(session, menu, "recipe", before, _describe(new, found), user_id)
    audit(session, menu, "q_factor", menu.q_factor_cents if not menu.q_factor_pct else f"{menu.q_factor_pct:g}%",
          q_factor_cents if not q_factor_pct else f"{q_factor_pct:g}%", user_id)
    menu.q_factor_cents, menu.q_factor_pct = q_factor_cents, q_factor_pct
    if lines and menu.recipe_since is None:
        menu.recipe_since = local_today(location)
    if not lines:
        menu.recipe_since = None
    await session.flush()


def _uses(costing: Costing, item_id: str, target: str, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return False
    for ln in costing.lines.get(item_id, []):
        if ln.ingredient_id == target or _uses(costing, ln.ingredient_id, target, depth + 1):
            return True
    return False


async def make_batches(session: AsyncSession, location: Location, prep: Item, batches: float,
                       user_id: str | None) -> dict:
    """The kitchen makes prep: its ingredients come out of stock, and the prep item goes in at what it cost."""
    if not is_prep(prep):
        raise InventoryError(f"{prep.name} isn't a prep item: give it a batch size first")
    costing = await load_costing(session, location)
    if not costing.has_recipe(prep.id):
        raise InventoryError(f"{prep.name} has no recipe yet")
    source = f"prep:{prep.id}:{utcnow().isoformat(timespec='seconds')}"
    used = 0
    for ln in costing.lines[prep.id]:
        c = costing.cost_line(ln)
        if c.ingredient is None or c.stock_qty is None:
            continue
        mv = await record(session, c.ingredient, "prep", -c.stock_qty * batches, source=source, user_id=user_id,
                          note=f"Used to make {batches:g} × {prep.name}")
        used -= mv.value_cents
    made = prep.batch_qty * batches
    await record(session, prep, "prep", made, unit_cost=used / made if made else None, source=source,
                 user_id=user_id, note=f"Made {batches:g} batch{'es' if batches != 1 else ''}")
    await session.flush()
    return {"made": made, "unit": prep.unit, "cost_cents": used, "stock_qty": prep.stock_qty}


async def snapshot_costs(session: AsyncSession, location: Location, reason: str,
                         menu_ids: list[str] | None = None) -> list[dict]:
    """Keep each menu item's plate cost history: a row whenever it (or the price) changes. Returns the changes."""
    costing = await load_costing(session, location)
    menu = [i for i in costing.items.values() if costing.has_recipe(i.id) and not is_prep(i) and i.price_cents > 0
            and i.location_id == location.id and (menu_ids is None or i.id in menu_ids)]
    if not menu:
        return []
    last: dict[str, PlateCostHistory] = {}
    for h in await session.scalars(select(PlateCostHistory).where(
            PlateCostHistory.menu_item_id.in_([m.id for m in menu])).order_by(PlateCostHistory.created_at.desc())):
        last.setdefault(h.menu_item_id, h)
    changes = []
    for m in menu:
        cost = round(costing.plate(m.id).cost_cents)
        prev = last.get(m.id)
        if prev is not None and abs(prev.plate_cost_cents - cost) < 1 and prev.price_cents == m.price_cents:
            continue
        session.add(PlateCostHistory(location_id=location.id, menu_item_id=m.id, plate_cost_cents=cost,
                                     price_cents=m.price_cents, reason=reason[:200]))
        if prev is not None and cost != prev.plate_cost_cents:
            target = target_for(m, location)
            pct = round(cost / m.price_cents * 100, 1)
            changes.append({"item_id": m.id, "name": m.name, "old_cents": prev.plate_cost_cents, "new_cents": cost,
                            "price_cents": m.price_cents, "food_cost_pct": pct, "target_pct": target,
                            "over_target": pct > target + 0.05})
    await session.flush()
    return changes


def local_today(location: Location) -> date:
    return utcnow().astimezone(ZoneInfo(location.timezone)).date()


def _day_end(day: date, tz: str) -> datetime:
    """Sales of a day go in the ledger at the end of that day (or now, for today)."""
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=ZoneInfo(tz)) - timedelta(seconds=1)
    return min(end, utcnow())


async def deplete_sales(session: AsyncSession, location: Location, days: set[date]) -> int:
    """Take ingredients out of stock for what sold on these days: units sold × recipe (EP portion ÷ yield), through
    prep items down to raw ingredients (unless the prep item is kept as stock itself).

    Kept in step with the day's sales: running it again after more orders, a void or a re-import adds only the
    difference, as new entries. Days before a menu item's recipe existed are left alone.
    """
    if not days:
        return 0
    costing = await load_costing(session, location)
    menu = [i for i in costing.items.values() if costing.has_recipe(i.id) and not is_prep(i)]
    if not menu:
        return 0
    by_name: dict[str, list[Item]] = {}
    for m in menu:
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
                for k, v in costing.explode(m.id, float(qty or 0)).items():
                    want[k] = want.get(k, 0.0) + v
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


def _gap(expected: float | None, delta: float, value: int) -> bool:
    if abs(value) >= BIG_GAP_CENTS:
        return True
    return bool(expected and abs(delta) / abs(expected) * 100 >= BIG_GAP_PCT and abs(delta) >= 1e-6)


async def submit_count(session: AsyncSession, location: Location, *, client_id: str, area_id: str | None,
                       lines: list[dict], user_id: str | None, counted_at: datetime | None = None,
                       started_at: datetime | None = None, approve: bool = False) -> StockCount:
    """A count from the phone: counted vs the system amount frozen when it started, and the $ difference.
    An owner or manager approves it (straight away, if they did the count) and only then does it change stock.
    Sent twice (an offline phone reconnecting), it's kept once."""
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
    out, variance = [], 0
    for ln in lines:
        item = items.get(ln["item_id"])
        if item is None:
            continue
        expected = ln.get("expected")
        expected = float(expected) if expected is not None else item.stock_qty
        counted = float(ln["qty"])
        delta = counted - (expected or 0.0)
        cost = item.avg_cost if item.avg_cost is not None else item.cost_cents
        value = round(delta * cost) if cost is not None else 0
        variance += value
        out.append({"item_id": item.id, "name": item.name, "unit": item.unit, "expected": expected,
                    "counted": counted, "delta": round(delta, 6), "value_cents": value,
                    "big": _gap(expected, delta, value)})
    now = utcnow()
    count = StockCount(location_id=location.id, storage_area_id=area.id if area else None,
                       area_name=area.name if area else "Everything", client_id=client_id, counted_by=user_id,
                       counted_at=min(counted_at or now, now), started_at=min(started_at, now) if started_at else None,
                       status="submitted", lines=out, variance_cents=variance)
    session.add(count)
    await session.flush()
    if approve:
        await approve_count(session, count, user_id)
    return count


async def approve_count(session: AsyncSession, count: StockCount, user_id: str | None) -> StockCount:
    """Post the count's differences to the ledger. Each item moves by counted − the amount frozen at the start, so
    sales and deliveries recorded while people were counting still count."""
    if count.status != "submitted":
        raise InventoryError(f"This count is already {count.status}")
    ids = [ln["item_id"] for ln in count.lines or []]
    items = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    lines, variance = [], 0
    for ln in count.lines or []:
        item = items.get(ln["item_id"])
        value = 0
        if item is not None:
            if ln.get("expected") is None:
                mv = await set_qty(session, item, ln["counted"], "count", source=f"count:{count.id}",
                                   user_id=user_id, note=f"Counted in {count.area_name}")
            elif abs(ln["delta"]) > 1e-9:
                mv = await record(session, item, "count", ln["delta"], source=f"count:{count.id}", user_id=user_id,
                                  note=f"Counted in {count.area_name}")
            else:
                mv = None
            value = mv.value_cents if mv is not None else 0
        variance += value
        lines.append({**ln, "value_cents": value})
    count.lines, count.variance_cents = lines, variance
    count.status, count.approved_by, count.approved_at = "approved", user_id, utcnow()
    await session.flush()
    return count


async def log_waste(session: AsyncSession, location: Location, item: Item, *, qty: float, unit: str | None,
                    reason: str, note: str | None, user_id: str | None, client_id: str | None = None,
                    at: datetime | None = None) -> StockMovement:
    """Throw something out: stock goes down at its cost. A waste entry synced twice is kept once."""
    if reason not in WASTE_REASONS:
        raise InventoryError("Pick a reason for the waste")
    source = f"waste:{client_id}" if client_id else "waste"
    if client_id:
        dup = await session.scalar(select(StockMovement).where(StockMovement.location_id == location.id,
                                                               StockMovement.source == source))
        if dup is not None:
            return dup
    amount = item_convert(qty, unit, item)
    if amount is None:
        raise InventoryError(f"{item.name} is counted in {item.unit or 'each'}, not {unit}")
    return await record(session, item, "waste", -abs(amount), source=source, reason=reason, note=note,
                        user_id=user_id, at=min(at or utcnow(), utcnow()))


# --- COGS and food cost -------------------------------------------------------------------------------------------


async def value_at(session: AsyncSession, location: Location, when: datetime) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(StockMovement.value_cents), 0)).where(
        StockMovement.location_id == location.id, StockMovement.created_at < when)) or 0)


async def cogs(session: AsyncSession, location: Location, start: date, end: date) -> dict:
    """Opening + purchases − closing for [start, end] (local days), the sales, and the actual food cost %, overall
    and by category. Also the variance per item: actual usage minus what the recipes say the sales used."""
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
    ids = {r[0] for r in rows if r[0]}
    info = {i.id: i for i in await session.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    per: dict[str, dict] = {}
    cats: dict[str, dict] = {}
    for item_id, name, kind, qty, value in rows:
        it = info.get(item_id)
        cat = (it.category if it is not None else None) or "Other"
        c = cats.setdefault(cat, {"category": cat, "received_cents": 0, "used_cents": 0, "waste_cents": 0})
        p = per.setdefault(item_id or name, {"item_id": item_id, "name": name, "category": cat,
                                             "theoretical_qty": 0.0, "variance_qty": 0.0, "variance_cents": 0,
                                             "waste_cents": 0, "sales_cents": 0})
        if kind in ADDED:
            c["received_cents"] += value or 0
        else:
            c["used_cents"] -= value or 0
        if kind == "sale":
            p["theoretical_qty"] -= qty or 0
            p["sales_cents"] -= value or 0
        elif kind in USAGE:
            p["variance_qty"] -= qty or 0  # used beyond what the recipes account for
            p["variance_cents"] -= value or 0
            if kind == "waste":
                p["waste_cents"] -= value or 0
                c["waste_cents"] -= value or 0
    variance = []
    for p in per.values():
        if abs(p["variance_qty"]) < 1e-9 and abs(p["theoretical_qty"]) < 1e-9:
            continue
        it = info.get(p["item_id"])
        p["unit"] = it.unit if it is not None else None
        p["actual_qty"] = round(p["theoretical_qty"] + p["variance_qty"], 4)
        p["variance_pct"] = (round(p["variance_qty"] / p["theoretical_qty"] * 100, 1)
                             if p["theoretical_qty"] > 1e-9 else None)
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
        "by_category": sorted((c for c in cats.values() if c["received_cents"] or c["used_cents"]),
                              key=lambda c: -c["used_cents"]),
        "variance": variance[:50],
    }
