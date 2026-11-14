"""Inventory: what's on hand and what it's worth, storage areas, counts, waste, adjustments, the stock ledger,
recipes and plate costs, and COGS with the actual food cost %.

Counting and logging waste are open to all staff (they're done in the walk-in, on a phone); costs, recipes and
adjustments need an owner or manager.
"""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import (Item, ItemSalesDaily, Location, RecipeLine, StockCount, StockMovement, StorageArea, Supplier,
                        User, utcnow)
from app.services import inventory as inv
from app.tenancy import require_location, require_manager

router = APIRouter(prefix="/locations/{location_id}", tags=["inventory"])

DEFAULT_AREAS = ["Walk-in cooler", "Freezer", "Dry storage", "Bar"]


def _422(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


async def _item(session: AsyncSession, location: Location, item_id: str) -> Item:
    item = await session.get(Item, item_id)
    if item is None or item.location_id != location.id:
        raise HTTPException(404, "Item not found")
    return item


async def _area(session: AsyncSession, location: Location, area_id: str | None) -> StorageArea | None:
    if not area_id:
        return None
    area = await session.get(StorageArea, area_id)
    if area is None or area.location_id != location.id:
        raise HTTPException(404, "Storage area not found")
    return area


def _cost(item: Item) -> float | None:
    return item.avg_cost if item.avg_cost is not None else (float(item.cost_cents) if item.cost_cents else None)


def item_out(item: Item, suppliers: dict[str, str], ingredient_of: dict[str, int] | None = None) -> dict:
    cost = _cost(item)
    return {
        "id": item.id, "name": item.name, "category": item.category, "unit": item.unit, "provider": item.provider,
        "stock_qty": item.stock_qty, "avg_cost": round(cost, 4) if cost is not None else None,
        "value_cents": round((item.stock_qty or 0) * cost) if cost is not None and item.stock_qty else 0,
        "par": item.low_stock_threshold, "reorder_qty": item.reorder_qty, "status": inv.status(item),
        "storage_area_id": item.storage_area_id, "supplier_id": item.supplier_id,
        "supplier": suppliers.get(item.supplier_id or ""), "price_cents": item.price_cents,
        "used_in": (ingredient_of or {}).get(item.id, 0), "editable_name": item.provider in ("pass", "demo"),
    }


# --- On hand ------------------------------------------------------------------------------------------------------


@router.get("/inventory/on-hand")
async def on_hand(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    """Tracked items (and every ingredient used in a recipe), by storage area, with their value."""
    await inv.ensure_openings(session, location)
    await session.commit()
    used = dict((await session.execute(
        select(RecipeLine.ingredient_id, func.count(func.distinct(RecipeLine.menu_item_id)))
        .where(RecipeLine.location_id == location.id).group_by(RecipeLine.ingredient_id))).all())
    items = list(await session.scalars(
        select(Item).where(Item.location_id == location.id,
                           (Item.stock_qty.is_not(None)) | (Item.id.in_(list(used) or [""])))
        .order_by(func.lower(Item.name))))
    areas = list(await session.scalars(select(StorageArea).where(StorageArea.location_id == location.id)
                                       .order_by(StorageArea.sort, StorageArea.name)))
    suppliers = {s.id: s.name for s in await session.scalars(select(Supplier).where(Supplier.location_id == location.id))}
    rows = [item_out(i, suppliers, used) for i in items]
    by_area: dict[str | None, list[dict]] = {}
    for r in rows:
        by_area.setdefault(r["storage_area_id"], []).append(r)
    untracked = await session.scalar(select(func.count(Item.id)).where(
        Item.location_id == location.id, Item.stock_qty.is_(None), Item.active.is_(True)))
    return {
        "items": rows,
        "areas": [{"id": a.id, "name": a.name, "sort": a.sort, "items": len(by_area.get(a.id, [])),
                   "value_cents": sum(r["value_cents"] for r in by_area.get(a.id, []))} for a in areas],
        "unassigned": len(by_area.get(None, [])),
        "totals": {"items": len(rows), "value_cents": sum(r["value_cents"] for r in rows),
                   "low": sum(r["status"] == "low" for r in rows), "out": sum(r["status"] == "out" for r in rows),
                   "negative": sum(r["status"] == "negative" for r in rows),
                   "no_cost": sum(r["avg_cost"] is None and r["stock_qty"] is not None for r in rows)},
        "untracked": untracked or 0,
        "target_food_cost_pct": location.target_food_cost_pct,
        "units": inv.UNITS, "waste_reasons": inv.WASTE_REASONS,
        "suggested_areas": DEFAULT_AREAS if not areas else [],
    }


class StockItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=200)
    storage_area_id: str | None = None
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)  # cents per unit
    qty: float | None = Field(default=None, ge=0, le=10_000_000)
    par: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_qty: float | None = Field(default=None, ge=0, le=10_000_000)
    supplier_id: str | None = None


@router.post("/inventory/items")
async def add_stock_item(body: StockItemIn, location: Location = Depends(require_manager),
                         user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """An ingredient or supply that isn't sold on its own (kept off the customer menu)."""
    await _area(session, location, body.storage_area_id)
    name = body.name.strip()
    if await session.scalar(select(Item.id).where(Item.location_id == location.id,
                                                  func.lower(Item.name) == name.lower())):
        raise HTTPException(409, f"There's already an item called {name}")
    if body.supplier_id:
        sup = await session.get(Supplier, body.supplier_id)
        if sup is None or sup.location_id != location.id:
            raise HTTPException(404, "Supplier not found")
    item = Item(location_id=location.id, provider="pass", external_id=f"pass-{uuid.uuid4().hex[:12]}", name=name,
                category=(body.category or "").strip() or "Ingredients", unit=inv.norm_unit(body.unit) if body.unit
                else None, price_cents=0, orderable=False, active=True, storage_area_id=body.storage_area_id,
                low_stock_threshold=body.par, reorder_qty=body.reorder_qty, supplier_id=body.supplier_id)
    session.add(item)
    await session.flush()
    await inv.record(session, item, "opening", float(body.qty or 0), unit_cost=body.unit_cost, source="opening",
                     user_id=user.id, note="Added to inventory")
    if body.unit_cost is not None and not body.qty:
        item.avg_cost, item.cost_cents = float(body.unit_cost), round(body.unit_cost)
    await session.commit()
    return {"id": item.id}


class StockItemEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=32)
    storage_area_id: str | None = None
    par: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_qty: float | None = Field(default=None, ge=0, le=10_000_000)
    supplier_id: str | None = None
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)
    track: bool | None = None  # start (or stop) keeping count of an item


@router.put("/inventory/items/{item_id}")
async def edit_stock_item(item_id: str, body: StockItemEdit, location: Location = Depends(require_manager),
                          user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    item = await _item(session, location, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "storage_area_id" in changes:
        await _area(session, location, changes["storage_area_id"])
        item.storage_area_id = changes["storage_area_id"]
    if changes.get("supplier_id"):
        sup = await session.get(Supplier, changes["supplier_id"])
        if sup is None or sup.location_id != location.id:
            raise HTTPException(404, "Supplier not found")
    if "supplier_id" in changes:
        item.supplier_id = changes["supplier_id"]
    if changes.get("name"):
        if item.provider not in ("pass", "demo"):
            raise HTTPException(422, "This item comes from your POS: rename it on the Menu items tab.")
        item.name = changes["name"].strip()
    if "unit" in changes:
        item.unit = inv.norm_unit(changes["unit"]) if changes["unit"] else None
    if "par" in changes:
        item.low_stock_threshold = changes["par"]
    if "reorder_qty" in changes:
        item.reorder_qty = changes["reorder_qty"]
    if changes.get("track") is True and item.stock_qty is None:
        await inv.record(session, item, "adjust", 0.0, user_id=user.id, note="Started keeping count")
    elif changes.get("track") is False:
        item.stock_qty = None
    if changes.get("unit_cost") is not None and changes["unit_cost"] != _cost(item):
        await inv.revalue(session, item, changes["unit_cost"], user_id=user.id)
    await session.commit()
    return {"ok": True}


class AdjustIn(BaseModel):
    item_id: str
    qty_delta: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    set_to: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    reason: str = Field(default="correction", max_length=32)
    note: str | None = Field(default=None, max_length=500)


@router.post("/stock-adjustments")
async def adjust(body: AdjustIn, location: Location = Depends(require_manager),
                 user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """A correction by hand: add or take away an amount, or set what's there. Written in the ledger."""
    item = await _item(session, location, body.item_id)
    if body.set_to is not None:
        mv = await inv.set_qty(session, item, body.set_to, "adjust", reason=body.reason, note=body.note,
                               user_id=user.id)
    elif body.qty_delta:
        mv = await inv.record(session, item, "adjust", body.qty_delta, reason=body.reason, note=body.note,
                              user_id=user.id)
    else:
        raise HTTPException(422, "Enter an amount")
    await session.commit()
    return {"ok": True, "stock_qty": item.stock_qty, "value_cents": mv.value_cents if mv else 0}


# --- Storage areas ------------------------------------------------------------------------------------------------


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort: int | None = Field(default=None, ge=0, le=1000)


@router.post("/storage-areas")
async def add_area(body: AreaIn, location: Location = Depends(require_manager),
                   session: AsyncSession = Depends(get_session)):
    n = await session.scalar(select(func.count(StorageArea.id)).where(StorageArea.location_id == location.id)) or 0
    area = StorageArea(location_id=location.id, name=body.name.strip(), sort=body.sort if body.sort is not None else n)
    session.add(area)
    await session.commit()
    return {"id": area.id, "name": area.name}


@router.post("/storage-areas/defaults")
async def add_default_areas(location: Location = Depends(require_manager),
                            session: AsyncSession = Depends(get_session)):
    have = {a.lower() for a in await session.scalars(select(StorageArea.name).where(
        StorageArea.location_id == location.id))}
    for n, name in enumerate(DEFAULT_AREAS):
        if name.lower() not in have:
            session.add(StorageArea(location_id=location.id, name=name, sort=n))
    await session.commit()
    return {"ok": True}


@router.put("/storage-areas/{area_id}")
async def edit_area(area_id: str, body: AreaIn, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    area = await _area(session, location, area_id)
    area.name = body.name.strip()
    if body.sort is not None:
        area.sort = body.sort
    await session.commit()
    return {"ok": True}


@router.delete("/storage-areas/{area_id}")
async def delete_area(area_id: str, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    """Its items stay in inventory, without an area."""
    area = await _area(session, location, area_id)
    for item in await session.scalars(select(Item).where(Item.storage_area_id == area.id)):
        item.storage_area_id = None
    await session.delete(area)
    await session.commit()
    return {"ok": True}


class AreaItemsIn(BaseModel):
    item_ids: list[str] = Field(max_length=1000)
    storage_area_id: str | None = None


@router.post("/storage-areas/assign")
async def assign_area(body: AreaItemsIn, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    await _area(session, location, body.storage_area_id)
    for item in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                         Item.id.in_(body.item_ids))):
        item.storage_area_id = body.storage_area_id
    await session.commit()
    return {"ok": True}


# --- Counts -------------------------------------------------------------------------------------------------------


class CountLine(BaseModel):
    item_id: str
    qty: float = Field(ge=0, le=10_000_000)


class CountIn(BaseModel):
    client_id: str = Field(min_length=6, max_length=64)
    storage_area_id: str | None = None
    lines: list[CountLine] = Field(min_length=1, max_length=2000)
    counted_at: datetime | None = None


def count_out(c: StockCount, names: dict[str, str]) -> dict:
    return {"id": c.id, "area": c.area_name, "storage_area_id": c.storage_area_id,
            "counted_at": c.counted_at.isoformat(), "counted_by": names.get(c.counted_by or ""),
            "items": len(c.lines or []), "changed": sum(1 for ln in c.lines or [] if abs(ln.get("delta") or 0) > 1e-9),
            "variance_cents": c.variance_cents, "lines": c.lines}


async def _names(session: AsyncSession, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.name or u.email.split("@")[0]) for u in await session.scalars(select(User).where(User.id.in_(ids)))}


@router.post("/stock-counts")
async def finish_count(body: CountIn, location: Location = Depends(require_location),
                       user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """A finished count from the phone (it may have been made offline). Sending the same count twice is safe."""
    try:
        count = await inv.finish_count(session, location, client_id=body.client_id, area_id=body.storage_area_id,
                                       lines=[ln.model_dump() for ln in body.lines], user_id=user.id,
                                       counted_at=body.counted_at)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    await session.commit()
    return count_out(count, await _names(session, {user.id}))


@router.get("/stock-counts")
async def list_counts(limit: int = Query(20, ge=1, le=200), location: Location = Depends(require_location),
                      session: AsyncSession = Depends(get_session)):
    counts = list(await session.scalars(select(StockCount).where(StockCount.location_id == location.id)
                                        .order_by(StockCount.counted_at.desc()).limit(limit)))
    names = await _names(session, {c.counted_by for c in counts})
    return {"counts": [count_out(c, names) for c in counts]}


# --- Waste --------------------------------------------------------------------------------------------------------


class WasteIn(BaseModel):
    item_id: str
    qty: float = Field(gt=0, le=10_000_000)
    unit: str | None = Field(default=None, max_length=32)
    reason: str = Field(max_length=32)
    note: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, min_length=6, max_length=64)
    at: datetime | None = None


def movement_out(m: StockMovement, names: dict[str, str]) -> dict:
    return {"id": m.id, "item_id": m.item_id, "item": m.item_name, "kind": m.kind, "qty_delta": m.qty_delta,
            "qty_after": m.qty_after, "unit": m.unit, "unit_cost": round(m.unit_cost, 4) if m.unit_cost is not None
            else None, "value_cents": m.value_cents, "source": m.source, "reason": m.reason, "note": m.note,
            "user": names.get(m.user_id or ""), "at": m.created_at.isoformat()}


@router.post("/waste")
async def log_waste(body: WasteIn, location: Location = Depends(require_location),
                    user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    item = await _item(session, location, body.item_id)
    try:
        mv = await inv.log_waste(session, location, item, qty=body.qty, unit=body.unit, reason=body.reason,
                                 note=body.note, user_id=user.id, client_id=body.client_id, at=body.at)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    await session.commit()
    return movement_out(mv, await _names(session, {user.id}))


@router.get("/waste")
async def list_waste(days: int = Query(30, ge=1, le=366), location: Location = Depends(require_location),
                     session: AsyncSession = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    rows = list(await session.scalars(select(StockMovement).where(
        StockMovement.location_id == location.id, StockMovement.kind == "waste",
        StockMovement.created_at >= since).order_by(StockMovement.created_at.desc()).limit(500)))
    names = await _names(session, {m.user_id for m in rows})
    by_reason: dict[str, int] = {}
    for m in rows:
        by_reason[m.reason or "other"] = by_reason.get(m.reason or "other", 0) - m.value_cents
    return {"days": days, "entries": [movement_out(m, names) for m in rows],
            "total_cents": -sum(m.value_cents for m in rows),
            "by_reason": [{"reason": r, "label": inv.WASTE_REASONS.get(r, r), "value_cents": v}
                          for r, v in sorted(by_reason.items(), key=lambda kv: -kv[1])]}


# --- The ledger ---------------------------------------------------------------------------------------------------


@router.get("/stock-movements")
async def ledger(item_id: str | None = None, kind: str | None = None, limit: int = Query(200, ge=1, le=1000),
                 before: datetime | None = None, location: Location = Depends(require_manager),
                 session: AsyncSession = Depends(get_session)):
    q = select(StockMovement).where(StockMovement.location_id == location.id)
    if item_id:
        q = q.where(StockMovement.item_id == item_id)
    if kind:
        q = q.where(StockMovement.kind == kind)
    if before:
        q = q.where(StockMovement.created_at < before)
    rows = list(await session.scalars(q.order_by(StockMovement.created_at.desc(), StockMovement.id).limit(limit)))
    names = await _names(session, {m.user_id for m in rows})
    return {"movements": [movement_out(m, names) for m in rows], "more": len(rows) == limit}


# --- Recipes ------------------------------------------------------------------------------------------------------


async def _sold(session: AsyncSession, location: Location, days: int = 30) -> dict[str, tuple[float, int]]:
    since = inv.local_today(location) - timedelta(days=days)
    rows = await session.execute(select(func.lower(ItemSalesDaily.name), func.sum(ItemSalesDaily.qty),
                                        func.sum(ItemSalesDaily.revenue_cents))
                                 .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day >= since)
                                 .group_by(func.lower(ItemSalesDaily.name)))
    return {name: (float(q or 0), int(r or 0)) for name, q, r in rows}


@router.get("/recipes")
async def list_recipes(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Every menu item with its plate cost and food cost %, and the recipe to-do list: best sellers with none."""
    menu = list(await session.scalars(select(Item).where(Item.location_id == location.id, Item.active.is_(True),
                                                         Item.price_cents > 0).order_by(func.lower(Item.name))))
    plates = await inv.recipes(session, location, [m.id for m in menu])
    sold = await _sold(session, location)
    target = location.target_food_cost_pct
    rows = []
    for m in menu:
        plate = plates.get(m.id)
        qty, revenue = sold.get(m.name.strip().lower(), (0.0, 0))
        row = {"id": m.id, "name": m.name, "category": m.category, "price_cents": m.price_cents,
               "has_recipe": plate is not None, "ingredients": len(plate.lines) if plate else 0,
               "complete": plate.complete if plate else False, "sold_30d": qty, "revenue_30d_cents": revenue,
               "plate_cost_cents": None, "food_cost_pct": None, "suggested_price_cents": None, "margin_cents": None}
        if plate is not None:
            row.update(inv.pricing(m.price_cents, plate.cost_cents, target))
        rows.append(row)
    todo = sorted((r for r in rows if not r["has_recipe"] and r["sold_30d"] > 0),
                  key=lambda r: (-r["revenue_30d_cents"], -r["sold_30d"]))[:10]
    costed = [r for r in rows if r["food_cost_pct"] is not None and r["sold_30d"] > 0]
    weighted = (sum(r["plate_cost_cents"] * r["sold_30d"] for r in costed)
                / max(1, sum(r["price_cents"] * r["sold_30d"] for r in costed)) * 100) if costed else None
    return {"menu": rows, "todo": todo, "target_food_cost_pct": target,
            "with_recipe": sum(r["has_recipe"] for r in rows),
            "theoretical_pct": round(weighted, 1) if weighted is not None else None}


async def recipe_detail(session: AsyncSession, location: Location, menu: Item) -> dict:
    plate = (await inv.recipes(session, location, [menu.id])).get(menu.id) or inv.PlateCost(
        q_factor_cents=int(menu.q_factor_cents or 0))
    lines = []
    for c in plate.lines:
        ing = c.ingredient
        lines.append({"id": c.line.id, "ingredient_id": c.line.ingredient_id, "name": ing.name if ing else "(deleted)",
                      "qty": c.line.qty, "unit": c.line.unit or (ing.unit if ing else None),
                      "stock_unit": ing.unit if ing else None, "yield_pct": c.line.yield_pct,
                      "stock_qty": round(c.stock_qty, 4) if c.stock_qty is not None else None,
                      "unit_cost": round(_cost(ing), 4) if ing and _cost(ing) is not None else None,
                      "cost_cents": round(c.cost_cents, 2) if c.cost_cents is not None else None,
                      "problem": c.problem})
    return {"id": menu.id, "name": menu.name, "category": menu.category, "price_cents": menu.price_cents,
            "q_factor_cents": menu.q_factor_cents, "recipe_since": menu.recipe_since.isoformat()
            if menu.recipe_since else None, "lines": lines, "complete": plate.complete,
            "target_food_cost_pct": location.target_food_cost_pct,
            **inv.pricing(menu.price_cents, plate.cost_cents, location.target_food_cost_pct)}


@router.get("/recipes/{item_id}")
async def get_recipe(item_id: str, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    return await recipe_detail(session, location, await _item(session, location, item_id))


class RecipeLineIn(BaseModel):
    ingredient_id: str
    qty: float = Field(gt=0, le=1_000_000)
    unit: str | None = Field(default=None, max_length=32)
    yield_pct: float = Field(default=100, ge=1, le=100)


class RecipeIn(BaseModel):
    lines: list[RecipeLineIn] = Field(max_length=60)
    q_factor_cents: int | None = Field(default=None, ge=0, le=100_000)


@router.put("/recipes/{item_id}")
async def save_recipe(item_id: str, body: RecipeIn, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    """Replace the recipe. From today, each one sold takes its ingredients out of stock."""
    menu = await _item(session, location, item_id)
    try:
        await inv.save_recipe(session, location, menu, [ln.model_dump() for ln in body.lines], body.q_factor_cents)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    await inv.deplete_sales(session, location, {inv.local_today(location)})
    await session.commit()
    return await recipe_detail(session, location, menu)


class SettingsIn(BaseModel):
    target_food_cost_pct: float = Field(ge=5, le=90)


@router.put("/inventory/settings")
async def inventory_settings(body: SettingsIn, location: Location = Depends(require_manager),
                             session: AsyncSession = Depends(get_session)):
    location.target_food_cost_pct = body.target_food_cost_pct
    await session.commit()
    return {"ok": True}


# --- COGS ---------------------------------------------------------------------------------------------------------


@router.get("/inventory/cogs")
async def cogs(start: date | None = None, end: date | None = None, location: Location = Depends(require_manager),
               session: AsyncSession = Depends(get_session)):
    """Opening + received − closing value for the period, the actual food cost %, and where usage went beyond
    what the recipes account for (waste and count differences)."""
    end = end or inv.local_today(location)
    start = start or end - timedelta(days=6)
    if start > end:
        raise HTTPException(422, "The start date is after the end date")
    if (end - start).days > 400:
        raise HTTPException(422, "Choose up to 400 days")
    await inv.ensure_openings(session, location)
    await session.commit()
    return await inv.cogs(session, location, start, end)
