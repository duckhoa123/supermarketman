"""Dashboard, inventory, orders, tables, reviews and alerts for one location."""

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.integrations.pos.base import POSAuthError
from app.integrations.reviews.google import get_google_client
from app.models import Alert, AlertRule, DiningTable, Item, Location, Review, utcnow
from app.services import analytics, demo_data, pos_import, pos_write
from app.services.integration_store import access_token, get_integration
from app.tenancy import require_location, require_manager, require_owner

router = APIRouter(prefix="/locations/{location_id}", tags=["restaurant"])


def _bad_period(exc: ValueError):
    return HTTPException(422, str(exc))


@router.get("/dashboard")
async def get_dashboard(period: str = "today", location: Location = Depends(require_location),
                        session: AsyncSession = Depends(get_session)):
    try:
        return await analytics.dashboard(session, location, period)
    except ValueError as exc:
        raise _bad_period(exc) from exc


@router.get("/sales")
async def get_sales(period: str = "last_7_days", by: str = "day", location: Location = Depends(require_location),
                    session: AsyncSession = Depends(get_session)):
    try:
        return await analytics.sales_breakdown(session, location, period, by)
    except ValueError as exc:
        raise _bad_period(exc) from exc


@router.get("/items/top")
async def get_top_items(period: str = "last_7_days", sort_by: str = "revenue", limit: int = Query(10, le=50),
                        location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    try:
        return await analytics.top_items(session, location, period, sort_by, limit)
    except ValueError as exc:
        raise _bad_period(exc) from exc


@router.get("/inventory")
async def get_inventory(low_stock_only: bool = False, search: str | None = None,
                        location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    return await analytics.inventory(session, location, low_stock_only, search, limit=50)


class ItemPatch(BaseModel):
    """Only the fields sent are changed."""

    low_stock_threshold: float | None = Field(default=None, ge=0)
    reorder_qty: float | None = Field(default=None, ge=0)
    supplier_id: str | None = None
    cost_cents: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=1000)
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    tags: list[str] | None = None
    orderable: bool | None = None


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: ItemPatch, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    from app.models import Supplier

    item = await session.get(Item, item_id)
    if item is None or item.location_id != location.id:
        raise HTTPException(404, "Item not found")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("supplier_id"):
        supplier = await session.get(Supplier, changes["supplier_id"])
        if supplier is None or supplier.location_id != location.id:
            raise HTTPException(404, "Supplier not found")
    if "tags" in changes:
        changes["tags"] = sorted({t.strip().lower().replace(" ", "_") for t in changes["tags"] or [] if t.strip()})
    for key, value in changes.items():
        setattr(item, key, value)
    await session.commit()
    return {"ok": True}


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=127)
    price_cents: int = Field(ge=0, le=10_000_000)
    category: str | None = Field(default=None, max_length=100)
    stock_qty: float | None = Field(default=None, ge=0, le=1_000_000)


@router.post("/items")
async def add_item(body: ItemIn, location: Location = Depends(require_manager),
                   session: AsyncSession = Depends(get_session)):
    """A new menu item. With Clover connected it's created in Clover first."""
    try:
        item = await pos_write.add_item(session, location, name=body.name.strip(), price_cents=body.price_cents,
                                        category=(body.category or "").strip() or None, stock_qty=body.stock_qty)
    except pos_write.PosError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": item.id, "provider": item.provider}


class ItemEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=127)
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    category: str | None = Field(default=None, max_length=100)
    stock_qty: float | None = Field(default=None, ge=0, le=1_000_000)


@router.put("/items/{item_id}/details")
async def edit_item(item_id: str, body: ItemEdit, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    """Name, price, category and stock. Clover items are changed in Clover too."""
    item = await session.get(Item, item_id)
    if item is None or item.location_id != location.id:
        raise HTTPException(404, "Item not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    if "category" in changes:
        changes["category"] = (changes["category"] or "").strip() or None
    try:
        await pos_write.edit_item(session, location, item, changes)
    except pos_write.PosError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


class PosLineIn(BaseModel):
    item_id: str | None = None
    name: str | None = Field(default=None, max_length=127)
    unit_price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    qty: int = Field(default=1, ge=1, le=99)


class PosOrderIn(BaseModel):
    lines: list[PosLineIn] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=60)


@router.get("/pos-write")
async def pos_write_status(location: Location = Depends(require_location),
                           session: AsyncSession = Depends(get_session)):
    """Whether items and orders made in Pass go to a POS (and which)."""
    return await pos_write.status(session, location)


@router.post("/pos-orders")
async def send_pos_order(body: PosOrderIn, location: Location = Depends(require_manager),
                         session: AsyncSession = Depends(get_session)):
    """Ring up an order in Pass: it's created in Clover as an open order, to be paid at the Clover device."""
    for ln in body.lines:
        if not ln.item_id and not (ln.name and ln.unit_price_cents is not None):
            raise HTTPException(422, "Each line needs a menu item, or a name and a price")
    try:
        order = await pos_write.send_order(session, location, [ln.model_dump() for ln in body.lines],
                                           title=(body.title or "Order from Pass").strip(), note=body.note)
    except pos_write.PosError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": order.id, "pos_order_id": order.external_id, "total_cents": order.total_cents}


@router.get("/orders")
async def get_orders(period: str = "today", status: str | None = None, limit: int = Query(20, le=50),
                     location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    try:
        return await analytics.list_orders(session, location, period, status, None, limit)
    except ValueError as exc:
        raise _bad_period(exc) from exc


# --- Tables ---------------------------------------------------------------------------------------


@router.get("/tables")
async def get_tables(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    return await analytics.tables(session, location)


class TableIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    seats: int = Field(default=4, ge=1, le=50)


@router.post("/tables")
async def add_table(body: TableIn, location: Location = Depends(require_manager),
                    session: AsyncSession = Depends(get_session)):
    t = DiningTable(location_id=location.id, name=body.name, seats=body.seats)
    session.add(t)
    await session.commit()
    return {"id": t.id}


class TableStatus(BaseModel):
    status: str = Field(pattern="^(free|occupied|reserved|cleaning)$")


@router.patch("/tables/{table_id}")
async def set_table_status(table_id: str, body: TableStatus, location: Location = Depends(require_location),
                           session: AsyncSession = Depends(get_session)):
    t = await session.get(DiningTable, table_id)
    if t is None or t.location_id != location.id:
        raise HTTPException(404, "Table not found")
    t.status = body.status
    await session.commit()
    return {"ok": True}


# --- Reviews --------------------------------------------------------------------------------------


@router.get("/reviews")
async def get_reviews(period: str = "last_30_days", max_rating: int | None = None, unreplied_only: bool = False,
                      location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    try:
        return await analytics.list_reviews(session, location, period, max_rating, unreplied_only, limit=50)
    except ValueError as exc:
        raise _bad_period(exc) from exc


class ReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=4000)


@router.post("/reviews/{review_id}/reply")
async def reply_to_review(review_id: str, body: ReplyIn, location: Location = Depends(require_manager),
                          session: AsyncSession = Depends(get_session)):
    review = await session.get(Review, review_id)
    if review is None or review.location_id != location.id:
        raise HTTPException(404, "Review not found")
    if review.source != "google":
        raise HTTPException(400, f"Replying on {review.source} isn't supported yet; reply on the platform directly")
    integ = await get_integration(session, location.id, "google")
    if integ is None or integ.status != "active":
        raise HTTPException(409, "Connect Google in Settings first")
    try:
        token = await access_token(session, integ)
        await get_google_client().post_reply(token, review.external_id, body.text)
    except POSAuthError as exc:
        raise HTTPException(409, "Google connection expired; reconnect it in Settings") from exc
    review.reply_text = body.text
    review.replied_at = utcnow()
    await session.commit()
    return {"ok": True}


# --- Alerts ---------------------------------------------------------------------------------------


@router.get("/alerts")
async def get_alerts(unread_only: bool = False, limit: int = Query(50, le=200),
                     location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    stmt = select(Alert).where(Alert.location_id == location.id)
    if unread_only:
        stmt = stmt.where(Alert.read_at.is_(None))
    alerts = list(await session.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit)))
    unread = await session.scalar(
        select(func.count(Alert.id)).where(Alert.location_id == location.id, Alert.read_at.is_(None))
    )
    return {
        "unread": unread or 0,
        "alerts": [
            {"id": a.id, "kind": a.kind, "title": a.title, "body": a.body, "data": a.data,
             "created_at": a.created_at.isoformat(), "read": a.read_at is not None}
            for a in alerts
        ],
    }


@router.post("/alerts/read-all")
async def read_all(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    for a in await session.scalars(select(Alert).where(Alert.location_id == location.id, Alert.read_at.is_(None))):
        a.read_at = utcnow()
    await session.commit()
    return {"ok": True}


@router.post("/alerts/{alert_id}/read")
async def read_one(alert_id: str, location: Location = Depends(require_location),
                   session: AsyncSession = Depends(get_session)):
    a = await session.get(Alert, alert_id)
    if a is None or a.location_id != location.id:
        raise HTTPException(404, "Alert not found")
    a.read_at = a.read_at or utcnow()
    await session.commit()
    return {"ok": True}


@router.get("/alert-rules")
async def get_rules(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    rules = await session.scalars(select(AlertRule).where(AlertRule.location_id == location.id))
    return [{"id": r.id, "kind": r.kind, "channels": r.channels, "params": r.params, "enabled": r.enabled}
            for r in rules]


class RuleIn(BaseModel):
    kind: str = Field(
        pattern="^(bad_review|low_stock|sales_drop|daily_digest|weekly_report|monthly_report|integration_error|new_order|price_change)$")
    channels: list[str] = Field(default_factory=lambda: ["push"])
    params: dict = Field(default_factory=dict)
    enabled: bool = True


@router.put("/alert-rules")
async def upsert_rule(body: RuleIn, location: Location = Depends(require_manager),
                      session: AsyncSession = Depends(get_session)):
    channels = [c for c in body.channels if c in ("push", "email", "whatsapp", "sms")]
    rule = await session.scalar(
        select(AlertRule).where(AlertRule.location_id == location.id, AlertRule.kind == body.kind)
    )
    if rule is None:
        rule = AlertRule(location_id=location.id, kind=body.kind)
        session.add(rule)
    rule.channels, rule.params, rule.enabled = channels, body.params, body.enabled
    await session.commit()
    return {"id": rule.id}


@router.get("/demo-data")
async def demo_data_counts(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    """How much sample menu and sales data is left from the demo."""
    return await demo_data.counts(session, location)


@router.post("/demo-data/clear")
async def clear_demo_data(location: Location = Depends(require_owner), session: AsyncSession = Depends(get_session)):
    """Remove the demo menu items and sales, keeping everything from the POS."""
    return await demo_data.clear(session, location)


# --- Importing POS export files (Clover's Items and Orders exports) ---------------------------------------------------


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(pos_import.MAX_BYTES + 1)
    if len(data) > pos_import.MAX_BYTES:
        raise HTTPException(413, "Files must be under 15 MB: export a shorter date range")
    return data


KINDS = "^(items|orders|sales_report)$"


@router.post("/imports/preview")
async def import_preview(file: UploadFile = File(...), kind: str | None = Form(default=None, pattern=KINDS),
                         location: Location = Depends(require_manager)):
    """What's in an exported file, which column holds what (guessed), and the first rows, before importing."""
    try:
        return pos_import.preview(location, file.filename, await _read_upload(file), kind)
    except pos_import.PosImportError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/imports")
async def import_file(file: UploadFile = File(...), kind: str | None = Form(default=None, pattern=KINDS),
                      mapping: str | None = Form(default=None, max_length=5000), day: str | None = Form(default=None),
                      location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Import the file with the columns the owner confirmed in the preview."""
    try:
        cols = json.loads(mapping) if mapping else None
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Bad column choices") from exc
    if cols is not None and not isinstance(cols, dict):
        raise HTTPException(422, "Bad column choices")
    try:
        on = date.fromisoformat(day) if day else None
    except ValueError as exc:
        raise HTTPException(422, "Pick a date") from exc
    try:
        return await pos_import.run(session, location, file.filename, await _read_upload(file), kind, cols, on)
    except pos_import.PosImportError as exc:
        raise HTTPException(422, str(exc)) from exc
