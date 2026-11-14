"""Inventory: what's on hand and what it's worth, storage areas, counts (submitted by staff, approved by a manager),
waste, adjustments, the stock ledger, recipes and prep items with plate costs, COGS and food cost %.

Everyone can count and log waste (they're done on a phone in the walk-in). Costs, recipes, pars, approvals and
adjustments need an owner or manager; staff see quantities without costs.
"""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import (InventoryAudit, Item, ItemSalesDaily, Location, PlateCostHistory, RecipeLine, StockCount,
                        StockMovement, StorageArea, Supplier, User, utcnow)
from app.services import inventory as inv
from app.tenancy import require_location, require_manager, user_role

router = APIRouter(prefix="/locations/{location_id}", tags=["inventory"])

DEFAULT_AREAS = ["Walk-in cooler", "Freezer", "Dry storage", "Bar"]
USAGE_DAYS = 28  # average daily usage, for days of cover


def _422(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


async def _manager(session: AsyncSession, user: AuthUser, location: Location) -> bool:
    return await user_role(session, user.id, location) in ("owner", "manager")


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


async def _supplier(session: AsyncSession, location: Location, supplier_id: str | None) -> None:
    if supplier_id:
        sup = await session.get(Supplier, supplier_id)
        if sup is None or sup.location_id != location.id:
            raise HTTPException(404, "Supplier not found")


def _cost(item: Item) -> float | None:
    return item.avg_cost if item.avg_cost is not None else (float(item.cost_cents) if item.cost_cents else None)


async def _names(session: AsyncSession, ids: set) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.name or u.email.split("@")[0]) for u in await session.scalars(select(User).where(User.id.in_(ids)))}


# --- On hand ------------------------------------------------------------------------------------------------------


@router.get("/inventory/on-hand")
async def on_hand(location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    """Counted items (and every ingredient used in a recipe), in count-sheet order, with par status, last count,
    days of cover and value."""
    manager = await _manager(session, user, location)
    await inv.ensure_openings(session, location)
    await session.commit()
    used = dict((await session.execute(
        select(RecipeLine.ingredient_id, func.count(func.distinct(RecipeLine.menu_item_id)))
        .where(RecipeLine.location_id == location.id).group_by(RecipeLine.ingredient_id))).all())
    items = list(await session.scalars(
        select(Item).where(Item.location_id == location.id,
                           (Item.stock_qty.is_not(None)) | (Item.id.in_(list(used) or [""])) | Item.batch_qty.is_not(None))
        .order_by(func.coalesce(Item.shelf_order, 1_000_000), func.lower(Item.name))))
    areas = list(await session.scalars(select(StorageArea).where(StorageArea.location_id == location.id)
                                       .order_by(StorageArea.sort, StorageArea.name)))
    suppliers = {s.id: s.name for s in await session.scalars(select(Supplier).where(Supplier.location_id == location.id))}
    since = utcnow() - timedelta(days=USAGE_DAYS)
    usage = dict((await session.execute(
        select(StockMovement.item_id, func.sum(StockMovement.qty_delta)).where(
            StockMovement.location_id == location.id, StockMovement.created_at >= since,
            StockMovement.kind.in_(("sale", "waste", "prep")), StockMovement.qty_delta < 0)
        .group_by(StockMovement.item_id))).all())
    last_counts: dict[str, tuple[float, datetime]] = {}
    for item_id, qty_after, at in await session.execute(
            select(StockMovement.item_id, StockMovement.qty_after, StockMovement.created_at).where(
                StockMovement.location_id == location.id, StockMovement.kind == "count")
            .order_by(StockMovement.created_at.desc())):
        last_counts.setdefault(item_id, (qty_after, at))
    costing = await inv.load_costing(session, location)
    rows = []
    for i in items:
        cost = _cost(i)
        if inv.is_prep(i) and costing.has_recipe(i.id):
            cost = costing.unit_cost(i)[0]
        daily = -(usage.get(i.id) or 0) / USAGE_DAYS
        last = last_counts.get(i.id)
        row = {
            "id": i.id, "name": i.name, "category": i.category, "unit": i.unit, "provider": i.provider,
            "stock_qty": i.stock_qty, "status": inv.status(i), "storage_area_id": i.storage_area_id,
            "par_level": i.par_level, "reorder_point": i.low_stock_threshold, "reorder_qty": i.reorder_qty,
            "supplier_id": i.supplier_id, "supplier": suppliers.get(i.supplier_id or ""), "barcode": i.barcode,
            "shelf_order": i.shelf_order, "default_yield_pct": i.default_yield_pct, "conversions": i.conversions or [],
            "is_prep": inv.is_prep(i), "batch_qty": i.batch_qty, "batch_unit": i.batch_unit,
            "has_recipe": costing.has_recipe(i.id), "used_in": used.get(i.id, 0),
            "editable_name": i.provider in ("pass", "demo"), "price_cents": i.price_cents,
            "last_count": {"qty": last[0], "at": last[1].isoformat()} if last else None,
            "daily_usage": round(daily, 4) if daily else None,
            "days_of_cover": round(i.stock_qty / daily, 1) if daily and i.stock_qty is not None else None,
            "avg_cost": None, "value_cents": None,
        }
        if manager:
            row["avg_cost"] = round(cost, 4) if cost is not None else None
            row["value_cents"] = round((i.stock_qty or 0) * cost) if cost is not None and i.stock_qty else 0
        rows.append(row)
    by_area: dict[str | None, list[dict]] = {}
    for r in rows:
        by_area.setdefault(r["storage_area_id"], []).append(r)
    untracked = await session.scalar(select(func.count(Item.id)).where(
        Item.location_id == location.id, Item.stock_qty.is_(None), Item.active.is_(True)))

    def value(rs: list[dict]) -> int | None:
        return sum(r["value_cents"] or 0 for r in rs) if manager else None

    flags = ("approaching", "at_par", "below_par", "out", "negative")
    return {
        "items": rows,
        "areas": [{"id": a.id, "name": a.name, "sort": a.sort, "items": len(by_area.get(a.id, [])),
                   "value_cents": value(by_area.get(a.id, []))} for a in areas],
        "unassigned": len(by_area.get(None, [])),
        "totals": {"items": len(rows), "value_cents": value(rows),
                   **{f: sum(r["status"] == f for r in rows) for f in flags},
                   "no_cost": sum(r["avg_cost"] is None and r["stock_qty"] is not None for r in rows) if manager else 0},
        "untracked": untracked or 0, "can_manage": manager,
        "target_food_cost_pct": location.target_food_cost_pct,
        "target_beverage_cost_pct": location.target_beverage_cost_pct, "cost_method": location.cost_method,
        "units": inv.UNITS, "categories": inv.CATEGORIES, "waste_reasons": inv.WASTE_REASONS,
        "suggested_areas": DEFAULT_AREAS if not areas else [],
    }


class Conversion(BaseModel):
    qty: float = Field(default=1, gt=0, le=1_000_000)
    unit: str = Field(min_length=1, max_length=32)
    to_qty: float = Field(gt=0, le=1_000_000)
    to_unit: str = Field(min_length=1, max_length=32)


class StockItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=200)
    storage_area_id: str | None = None
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)  # cents per unit
    qty: float | None = Field(default=None, ge=0, le=10_000_000)
    par_level: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_point: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_qty: float | None = Field(default=None, ge=0, le=10_000_000)
    supplier_id: str | None = None
    barcode: str | None = Field(default=None, max_length=64)
    default_yield_pct: float | None = Field(default=None, ge=1, le=100)
    conversions: list[Conversion] | None = Field(default=None, max_length=10)
    batch_qty: float | None = Field(default=None, gt=0, le=1_000_000)  # a prep item: one batch makes this much


@router.post("/inventory/items")
async def add_stock_item(body: StockItemIn, location: Location = Depends(require_manager),
                         user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """An ingredient, supply or prep item that isn't sold on its own (kept off the customer menu)."""
    await _area(session, location, body.storage_area_id)
    await _supplier(session, location, body.supplier_id)
    name = body.name.strip()
    if await session.scalar(select(Item.id).where(Item.location_id == location.id,
                                                  func.lower(Item.name) == name.lower())):
        raise HTTPException(409, f"There's already an item called {name}")
    unit = inv.norm_unit(body.unit) if body.unit else None
    item = Item(location_id=location.id, provider="pass", external_id=f"pass-{uuid.uuid4().hex[:12]}", name=name,
                category=(body.category or "").strip() or ("Prep" if body.batch_qty else "Ingredients"), unit=unit,
                price_cents=0, orderable=False, active=True, storage_area_id=body.storage_area_id,
                par_level=body.par_level, low_stock_threshold=body.reorder_point, reorder_qty=body.reorder_qty,
                supplier_id=body.supplier_id, barcode=(body.barcode or "").strip() or None,
                default_yield_pct=body.default_yield_pct,
                conversions=[c.model_dump() for c in body.conversions] if body.conversions else None,
                batch_qty=body.batch_qty, batch_unit=unit if body.batch_qty else None)
    session.add(item)
    await session.flush()
    if not body.batch_qty or body.qty:
        await inv.record(session, item, "opening", float(body.qty or 0), unit_cost=body.unit_cost, source="opening",
                         user_id=user.id, note="Added to inventory")
    if body.unit_cost is not None and not body.qty:
        item.avg_cost, item.cost_cents = float(body.unit_cost), round(body.unit_cost)
    await session.commit()
    return {"id": item.id}


class StockItemEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=200)
    storage_area_id: str | None = None
    par_level: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_point: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_qty: float | None = Field(default=None, ge=0, le=10_000_000)
    supplier_id: str | None = None
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)
    barcode: str | None = Field(default=None, max_length=64)
    default_yield_pct: float | None = Field(default=None, ge=1, le=100)
    conversions: list[Conversion] | None = Field(default=None, max_length=10)
    batch_qty: float | None = Field(default=None, ge=0, le=1_000_000)  # 0 turns a prep item back into a plain one
    track: bool | None = None  # start (or stop) keeping count of an item


AUDITED = {"unit": "unit", "par_level": "par", "low_stock_threshold": "reorder point", "reorder_qty": "reorder qty",
           "default_yield_pct": "yield", "conversions": "conversions", "batch_qty": "batch size"}


@router.put("/inventory/items/{item_id}")
async def edit_stock_item(item_id: str, body: StockItemEdit, location: Location = Depends(require_manager),
                          user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    item = await _item(session, location, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "storage_area_id" in changes:
        await _area(session, location, changes["storage_area_id"])
        item.storage_area_id = changes["storage_area_id"]
    if "supplier_id" in changes:
        await _supplier(session, location, changes["supplier_id"])
        item.supplier_id = changes["supplier_id"]
    if changes.get("name") and changes["name"].strip() != item.name:
        if item.provider not in ("pass", "demo"):
            raise HTTPException(422, "This item comes from your POS: rename it on the Menu items tab.")
        inv.audit(session, item, "name", item.name, changes["name"].strip(), user.id)
        item.name = changes["name"].strip()
    new: dict = {}
    if "unit" in changes:
        new["unit"] = inv.norm_unit(changes["unit"]) if changes["unit"] else None
    for k, col in (("par_level", "par_level"), ("reorder_point", "low_stock_threshold"), ("reorder_qty", "reorder_qty"),
                   ("default_yield_pct", "default_yield_pct")):
        if k in changes:
            new[col] = changes[k]
    if "conversions" in changes:
        new["conversions"] = [c for c in changes["conversions"] or []] or None
    if "batch_qty" in changes:
        new["batch_qty"] = changes["batch_qty"] or None
        new["batch_unit"] = (new.get("unit", item.unit)) if changes["batch_qty"] else None
    for col, value in new.items():
        if col in AUDITED:
            inv.audit(session, item, AUDITED[col], getattr(item, col), value, user.id)
        setattr(item, col, value)
    if "category" in changes:
        item.category = (changes["category"] or "").strip() or None
    if "barcode" in changes:
        item.barcode = (changes["barcode"] or "").strip() or None
    if changes.get("track") is True and item.stock_qty is None:
        await inv.record(session, item, "adjust", 0.0, user_id=user.id, note="Started keeping count")
    elif changes.get("track") is False:
        item.stock_qty = None
    if changes.get("unit_cost") is not None and changes["unit_cost"] != _cost(item):
        await inv.revalue(session, item, changes["unit_cost"], user_id=user.id)
    await session.flush()
    if {"unit_cost", "conversions", "batch_qty", "unit"} & set(changes):
        await inv.snapshot_costs(session, location, f"{item.name} changed")
    await session.commit()
    return {"ok": True}


class AdjustIn(BaseModel):
    item_id: str
    qty_delta: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    set_to: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    reason: str = Field(min_length=2, max_length=32)
    note: str | None = Field(default=None, max_length=500)


@router.post("/stock-adjustments")
async def adjust(body: AdjustIn, location: Location = Depends(require_manager),
                 user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """A correction by hand (manager only, with a reason): add or take away an amount, or set what's there."""
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


class MakeIn(BaseModel):
    batches: float = Field(gt=0, le=1000)


@router.post("/inventory/prep/{item_id}/make")
async def make_prep(item_id: str, body: MakeIn, location: Location = Depends(require_location),
                    user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """The kitchen made a batch of a prep item: its ingredients come out of stock and the prep goes in."""
    item = await _item(session, location, item_id)
    try:
        out = await inv.make_batches(session, location, item, body.batches, user.id)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    await session.commit()
    if not await _manager(session, user, location):
        out.pop("cost_cents", None)
    return out


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
    item_ids: list[str] = Field(max_length=2000)
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


@router.post("/storage-areas/order")
async def order_items(body: AreaItemsIn, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    """The count sheet order: items in the order given (dragged into shelf order)."""
    pos = {item_id: n for n, item_id in enumerate(body.item_ids)}
    for item in await session.scalars(select(Item).where(Item.location_id == location.id,
                                                         Item.id.in_(body.item_ids))):
        item.shelf_order = pos[item.id]
    await session.commit()
    return {"ok": True}


# --- Counts -------------------------------------------------------------------------------------------------------


class CountLine(BaseModel):
    item_id: str
    qty: float = Field(ge=0, le=10_000_000)
    expected: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)  # system amount when it started


class CountIn(BaseModel):
    client_id: str = Field(min_length=6, max_length=64)
    storage_area_id: str | None = None
    lines: list[CountLine] = Field(min_length=1, max_length=2000)
    counted_at: datetime | None = None
    started_at: datetime | None = None
    approve: bool = False  # an owner or manager can approve their own count straight away


def count_out(c: StockCount, names: dict[str, str], manager: bool) -> dict:
    lines = c.lines or []
    if not manager:
        lines = [{k: v for k, v in ln.items() if k != "value_cents"} for ln in lines]
    return {"id": c.id, "area": c.area_name, "storage_area_id": c.storage_area_id, "status": c.status,
            "counted_at": c.counted_at.isoformat(), "counted_by": names.get(c.counted_by or ""),
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "approved_by": names.get(c.approved_by or ""),
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
            "items": len(lines), "changed": sum(1 for ln in lines if abs(ln.get("delta") or 0) > 1e-9),
            "big": sum(1 for ln in lines if ln.get("big")),
            "variance_cents": c.variance_cents if manager else None, "lines": lines}


@router.post("/stock-counts")
async def submit_count(body: CountIn, location: Location = Depends(require_location),
                       user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """A finished count from the phone (perhaps made offline). Sending the same count twice is safe."""
    manager = await _manager(session, user, location)
    try:
        count = await inv.submit_count(session, location, client_id=body.client_id, area_id=body.storage_area_id,
                                       lines=[ln.model_dump() for ln in body.lines], user_id=user.id,
                                       counted_at=body.counted_at, started_at=body.started_at,
                                       approve=body.approve and manager)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    await session.commit()
    return count_out(count, await _names(session, {user.id}), manager)


@router.get("/stock-counts")
async def list_counts(limit: int = Query(30, ge=1, le=200), status: str | None = None,
                      location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    q = select(StockCount).where(StockCount.location_id == location.id)
    if status:
        q = q.where(StockCount.status == status)
    counts = list(await session.scalars(q.order_by(StockCount.counted_at.desc()).limit(limit)))
    names = await _names(session, {c.counted_by for c in counts} | {c.approved_by for c in counts})
    manager = await _manager(session, user, location)
    return {"counts": [count_out(c, names, manager) for c in counts], "can_approve": manager}


async def _count(session: AsyncSession, location: Location, count_id: str) -> StockCount:
    count = await session.get(StockCount, count_id)
    if count is None or count.location_id != location.id:
        raise HTTPException(404, "Count not found")
    return count


@router.post("/stock-counts/{count_id}/approve")
async def approve_count(count_id: str, location: Location = Depends(require_manager),
                        user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """The differences go in the ledger, and the count closes the period for COGS."""
    count = await _count(session, location, count_id)
    try:
        await inv.approve_count(session, count, user.id)
    except inv.InventoryError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    return count_out(count, await _names(session, {count.counted_by, user.id}), True)


@router.post("/stock-counts/{count_id}/reject")
async def reject_count(count_id: str, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    """Throw the count away (counted the wrong shelf, say). Stock doesn't change."""
    count = await _count(session, location, count_id)
    if count.status != "submitted":
        raise HTTPException(409, f"This count is already {count.status}")
    count.status = "rejected"
    await session.commit()
    return {"ok": True}


# --- Waste --------------------------------------------------------------------------------------------------------


class WasteIn(BaseModel):
    item_id: str
    qty: float = Field(gt=0, le=10_000_000)
    unit: str | None = Field(default=None, max_length=32)
    reason: str = Field(max_length=32)
    note: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, min_length=6, max_length=64)
    at: datetime | None = None


def movement_out(m: StockMovement, names: dict[str, str], manager: bool = True) -> dict:
    return {"id": m.id, "item_id": m.item_id, "item": m.item_name, "kind": m.kind, "qty_delta": m.qty_delta,
            "qty_after": m.qty_after, "unit": m.unit,
            "unit_cost": round(m.unit_cost, 4) if m.unit_cost is not None and manager else None,
            "value_cents": m.value_cents if manager else None, "source": m.source, "reason": m.reason,
            "note": m.note, "user": names.get(m.user_id or ""), "at": m.created_at.isoformat()}


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
    return movement_out(mv, await _names(session, {user.id}), await _manager(session, user, location))


@router.get("/waste")
async def list_waste(days: int = Query(30, ge=1, le=366), location: Location = Depends(require_location),
                     user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    manager = await _manager(session, user, location)
    since = utcnow() - timedelta(days=days)
    rows = list(await session.scalars(select(StockMovement).where(
        StockMovement.location_id == location.id, StockMovement.kind == "waste",
        StockMovement.created_at >= since).order_by(StockMovement.created_at.desc()).limit(500)))
    names = await _names(session, {m.user_id for m in rows})
    by_reason: dict[str, list[int]] = {}
    for m in rows:
        r = by_reason.setdefault(m.reason or "other", [0, 0])
        r[0] -= m.value_cents
        r[1] += 1
    return {"days": days, "entries": [movement_out(m, names, manager) for m in rows],
            "total_cents": -sum(m.value_cents for m in rows) if manager else None,
            "by_reason": [{"reason": r, "label": inv.WASTE_REASONS.get(r, r), "entries": v[1],
                           "value_cents": v[0] if manager else None}
                          for r, v in sorted(by_reason.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))]}


# --- The ledger and the audit -------------------------------------------------------------------------------------


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


@router.get("/inventory/audit")
async def audit_log(item_id: str | None = None, limit: int = Query(100, ge=1, le=500),
                    location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Recipe, par, yield and unit changes: who, when, the old and the new value."""
    q = select(InventoryAudit).where(InventoryAudit.location_id == location.id)
    if item_id:
        q = q.where(InventoryAudit.item_id == item_id)
    rows = list(await session.scalars(q.order_by(InventoryAudit.created_at.desc()).limit(limit)))
    names = await _names(session, {r.user_id for r in rows})
    return {"changes": [{"id": r.id, "item_id": r.item_id, "item": r.item_name, "field": r.field, "old": r.old,
                         "new": r.new, "user": names.get(r.user_id or ""), "at": r.created_at.isoformat()}
                        for r in rows]}


# --- Recipes ------------------------------------------------------------------------------------------------------


async def _sold(session: AsyncSession, location: Location, days: int = 30) -> dict[str, tuple[float, int]]:
    since = inv.local_today(location) - timedelta(days=days)
    rows = await session.execute(select(func.lower(ItemSalesDaily.name), func.sum(ItemSalesDaily.qty),
                                        func.sum(ItemSalesDaily.revenue_cents))
                                 .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day >= since)
                                 .group_by(func.lower(ItemSalesDaily.name)))
    return {name: (float(q or 0), int(r or 0)) for name, q, r in rows}


async def _previous_costs(session: AsyncSession, ids: list[str]) -> dict[str, tuple[int, datetime]]:
    """Each item's plate cost before its latest change (for "change since last time")."""
    if not ids:
        return {}
    seen: dict[str, int] = {}
    out: dict[str, tuple[int, datetime]] = {}
    for h in await session.scalars(select(PlateCostHistory).where(PlateCostHistory.menu_item_id.in_(ids))
                                   .order_by(PlateCostHistory.created_at.desc())):
        if h.menu_item_id not in seen:
            seen[h.menu_item_id] = h.plate_cost_cents
        elif h.menu_item_id not in out and h.plate_cost_cents != seen[h.menu_item_id]:
            out[h.menu_item_id] = (h.plate_cost_cents, h.created_at)
    return out


@router.get("/recipes")
async def list_recipes(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """The menu cost report: every menu item with plate cost, food cost %, margin and the change since last time,
    flagged when over target; prep items with their batch cost; and the to-do list of sellers with no recipe."""
    await inv.snapshot_costs(session, location, "First costed")  # starts the history; a no-op when nothing changed
    await session.commit()
    costing = await inv.load_costing(session, location)
    menu = list(await session.scalars(select(Item).where(Item.location_id == location.id, Item.active.is_(True),
                                                         Item.price_cents > 0, Item.batch_qty.is_(None))
                                      .order_by(func.lower(Item.name))))
    prep = list(await session.scalars(select(Item).where(Item.location_id == location.id, Item.batch_qty.is_not(None))
                                      .order_by(func.lower(Item.name))))
    sold = await _sold(session, location)
    previous = await _previous_costs(session, [m.id for m in menu])
    rows = []
    for m in menu:
        has = costing.has_recipe(m.id)
        plate = costing.plate(m.id) if has else None
        qty, revenue = sold.get(m.name.strip().lower(), (0.0, 0))
        row = {"id": m.id, "name": m.name, "category": m.category, "price_cents": m.price_cents,
               "has_recipe": has, "ingredients": len(plate.lines) if plate else 0,
               "complete": plate.complete if plate else False, "sold_30d": qty, "revenue_30d_cents": revenue,
               "plate_cost_cents": None, "food_cost_pct": None, "suggested_price_cents": None, "margin_cents": None,
               "target_pct": inv.target_for(m, location), "over_target": False,
               "previous_cost_cents": None, "previous_at": None}
        if plate is not None:
            row.update(inv.pricing(m.price_cents, plate.cost_cents, inv.target_for(m, location)))
            if m.id in previous:
                row["previous_cost_cents"], at = previous[m.id]
                row["previous_at"] = at.isoformat()
        rows.append(row)
    todo = sorted((r for r in rows if not r["has_recipe"] and r["sold_30d"] > 0),
                  key=lambda r: (-r["revenue_30d_cents"], -r["sold_30d"]))
    costed = [r for r in rows if r["food_cost_pct"] is not None and r["sold_30d"] > 0]
    weighted = (sum(r["plate_cost_cents"] * r["sold_30d"] for r in costed)
                / max(1, sum(r["price_cents"] * r["sold_30d"] for r in costed)) * 100) if costed else None
    preps = []
    for p in prep:
        batch = costing.plate(p.id) if costing.has_recipe(p.id) else None
        unit_cost = costing.unit_cost(p)[0] if batch else None
        preps.append({"id": p.id, "name": p.name, "unit": p.unit, "batch_qty": p.batch_qty,
                      "has_recipe": batch is not None, "complete": batch.complete if batch else False,
                      "ingredients": len(batch.lines) if batch else 0,
                      "batch_cost_cents": round(batch.cost_cents) if batch else None,
                      "unit_cost": round(unit_cost, 4) if unit_cost is not None else None,
                      "stock_qty": p.stock_qty})
    return {"menu": rows, "prep": preps, "todo": todo, "target_food_cost_pct": location.target_food_cost_pct,
            "target_beverage_cost_pct": location.target_beverage_cost_pct,
            "with_recipe": sum(r["has_recipe"] for r in rows), "sellers": sum(r["sold_30d"] > 0 for r in rows),
            "over_target": sum(r["over_target"] for r in rows),
            "theoretical_pct": round(weighted, 1) if weighted is not None else None}


async def recipe_detail(session: AsyncSession, location: Location, menu: Item) -> dict:
    costing = await inv.load_costing(session, location)
    plate = costing.plate(menu.id)
    lines = []
    for c in plate.lines:
        ing = c.ingredient
        lines.append({"id": c.line.id, "ingredient_id": c.line.ingredient_id, "name": ing.name if ing else "(deleted)",
                      "qty": c.line.qty, "unit": c.line.unit or (ing.unit if ing else None),
                      "stock_unit": ing.unit if ing else None, "yield_pct": c.line.yield_pct,
                      "is_prep": inv.is_prep(ing) if ing else False,
                      "conversions": (ing.conversions or []) if ing else [],
                      "stock_qty": round(c.stock_qty, 4) if c.stock_qty is not None else None,
                      "unit_cost": round(c.unit_cost, 4) if c.unit_cost is not None else None,
                      "cost_cents": round(c.cost_cents, 2) if c.cost_cents is not None else None,
                      "problem": c.problem})
    history = list(await session.scalars(select(PlateCostHistory).where(PlateCostHistory.menu_item_id == menu.id)
                                         .order_by(PlateCostHistory.created_at.desc()).limit(20)))
    target = inv.target_for(menu, location)
    out = {"id": menu.id, "name": menu.name, "category": menu.category, "price_cents": menu.price_cents,
           "is_prep": inv.is_prep(menu), "batch_qty": menu.batch_qty, "unit": menu.unit,
           "q_factor_cents": menu.q_factor_cents, "q_factor_pct": menu.q_factor_pct, "recipe_notes": menu.recipe_notes,
           "item_target_pct": menu.target_food_cost_pct,
           "recipe_since": menu.recipe_since.isoformat() if menu.recipe_since else None, "lines": lines,
           "complete": plate.complete, "target_food_cost_pct": target,
           "ingredients_cents": round(plate.ingredients_cents, 2), "q_cents": round(plate.q_cents, 2),
           "history": [{"plate_cost_cents": h.plate_cost_cents, "price_cents": h.price_cents, "reason": h.reason,
                        "at": h.created_at.isoformat()} for h in history],
           **inv.pricing(menu.price_cents, plate.cost_cents, target)}
    if inv.is_prep(menu) and plate.lines:
        out["unit_cost"] = round(plate.cost_cents / menu.batch_qty, 4)
    return out


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
    q_factor_pct: float | None = Field(default=None, ge=0, le=50)
    recipe_notes: str | None = Field(default=None, max_length=5000)
    target_food_cost_pct: float | None = Field(default=None, ge=5, le=90)  # this item only; empty = the default
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000)  # a Pass item's menu price


@router.put("/recipes/{item_id}")
async def save_recipe(item_id: str, body: RecipeIn, location: Location = Depends(require_manager),
                      user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Replace the recipe (one plate, or one batch of a prep item). From today, each one sold takes its ingredients
    out of stock."""
    menu = await _item(session, location, item_id)
    try:
        await inv.save_recipe(session, location, menu, [ln.model_dump() for ln in body.lines], body.q_factor_cents,
                              body.q_factor_pct, user_id=user.id)
    except inv.InventoryError as exc:
        raise _422(exc) from exc
    if "recipe_notes" in body.model_fields_set:
        menu.recipe_notes = (body.recipe_notes or "").strip() or None
    if "target_food_cost_pct" in body.model_fields_set:
        inv.audit(session, menu, "target %", menu.target_food_cost_pct, body.target_food_cost_pct, user.id)
        menu.target_food_cost_pct = body.target_food_cost_pct
    if body.price_cents is not None and body.price_cents != menu.price_cents:
        if menu.provider not in ("pass", "demo"):
            raise HTTPException(422, "This dish's price comes from your POS: change it on the Menu items tab.")
        inv.audit(session, menu, "price", menu.price_cents, body.price_cents, user.id)
        menu.price_cents = body.price_cents
    await session.flush()
    await inv.snapshot_costs(session, location, "Recipe changed")
    await inv.deplete_sales(session, location, {inv.local_today(location)})
    await session.commit()
    return await recipe_detail(session, location, menu)


class SettingsIn(BaseModel):
    target_food_cost_pct: float | None = Field(default=None, ge=5, le=90)
    target_beverage_cost_pct: float | None = Field(default=None, ge=5, le=90)
    cost_method: str | None = Field(default=None, pattern="^(average|last)$")


@router.put("/inventory/settings")
async def inventory_settings(body: SettingsIn, location: Location = Depends(require_manager),
                             session: AsyncSession = Depends(get_session)):
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(location, k, v)
    await session.commit()
    return {"ok": True}


# --- COGS ---------------------------------------------------------------------------------------------------------


@router.get("/inventory/cogs")
async def cogs(start: date | None = None, end: date | None = None, location: Location = Depends(require_manager),
               session: AsyncSession = Depends(get_session)):
    """Opening + purchases − closing value for the period, the actual food cost %, by category, and where usage
    went beyond what the recipes account for (waste and count differences)."""
    end = end or inv.local_today(location)
    start = start or end - timedelta(days=6)
    if start > end:
        raise HTTPException(422, "The start date is after the end date")
    if (end - start).days > 400:
        raise HTTPException(422, "Choose up to 400 days")
    await inv.ensure_openings(session, location)
    await session.commit()
    out = await inv.cogs(session, location, start, end)
    counts = list(await session.scalars(select(StockCount.approved_at).where(
        StockCount.location_id == location.id, StockCount.status == "approved", StockCount.approved_at.is_not(None))
        .order_by(StockCount.approved_at.desc()).limit(6)))
    out["counts"] = [c.isoformat() for c in counts]  # periods close at approved counts
    return out
