"""Dashboard, inventory, orders, tables, reviews and alerts for one location."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.integrations.pos.base import POSAuthError
from app.integrations.reviews.google import get_google_client
from app.models import Alert, AlertRule, DiningTable, Item, Location, Review, utcnow
from app.services import analytics
from app.services.integration_store import access_token, get_integration
from app.tenancy import require_location, require_manager

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
    low_stock_threshold: float | None = Field(default=None, ge=0)


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: ItemPatch, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    item = await session.get(Item, item_id)
    if item is None or item.location_id != location.id:
        raise HTTPException(404, "Item not found")
    item.low_stock_threshold = body.low_stock_threshold
    await session.commit()
    return {"ok": True}


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


@router.get("/alert-rules")
async def get_rules(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    rules = await session.scalars(select(AlertRule).where(AlertRule.location_id == location.id))
    return [{"id": r.id, "kind": r.kind, "channels": r.channels, "params": r.params, "enabled": r.enabled}
            for r in rules]


class RuleIn(BaseModel):
    kind: str = Field(pattern="^(bad_review|low_stock|sales_drop|daily_digest|integration_error)$")
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
