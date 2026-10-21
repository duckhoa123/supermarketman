"""Receiving: scan supplier invoices, check and confirm them, the receiving log sheet and price history."""

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.models import InvoiceScan, Location
from app.services import receiving
from app.tenancy import require_location

router = APIRouter(prefix="/locations/{location_id}", tags=["receiving"])


async def _scan(session: AsyncSession, location: Location, scan_id: str) -> InvoiceScan:
    scan = await session.get(InvoiceScan, scan_id)
    if scan is None or scan.location_id != location.id:
        raise HTTPException(404, "Invoice not found")
    return scan


@router.post("/invoices")
async def upload_invoice(file: UploadFile = File(...), location: Location = Depends(require_location),
                         user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Upload a photo or PDF of a supplier invoice. It's read in the background (usually under a minute)."""
    data = await file.read(int(get_settings().invoice_max_mb * 1024 * 1024) + 1)
    try:
        scan = await receiving.create_scan(session, location, user.id, file.filename, file.content_type, data)
    except receiving.ReceivingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return receiving.scan_out(scan, location.currency)


@router.get("/invoices")
async def list_invoices(status: str | None = None, limit: int = Query(30, le=100),
                        location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    stmt = select(InvoiceScan).where(InvoiceScan.location_id == location.id)
    if status:
        stmt = stmt.where(InvoiceScan.status == status)
    else:
        stmt = stmt.where(InvoiceScan.status != "discarded")
    rows = await session.scalars(stmt.order_by(InvoiceScan.created_at.desc()).limit(limit))
    return [receiving.scan_out(s, location.currency) for s in rows]


@router.get("/invoices/{scan_id}")
async def get_invoice(scan_id: str, location: Location = Depends(require_location),
                      session: AsyncSession = Depends(get_session)):
    return receiving.scan_out(await _scan(session, location, scan_id), location.currency)


@router.get("/invoices/{scan_id}/file")
async def invoice_file(scan_id: str, location: Location = Depends(require_location),
                       session: AsyncSession = Depends(get_session)):
    scan = await _scan(session, location, scan_id)
    data = (await session.execute(select(InvoiceScan.file_bytes).where(InvoiceScan.id == scan.id))).scalar_one()
    if not data:
        raise HTTPException(410, f"Invoice images are kept for {get_settings().invoice_file_days} days")
    return Response(content=data, media_type=scan.content_type or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600"})


class DraftLine(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    sku: str | None = Field(default=None, max_length=100)
    qty: float = Field(default=1, ge=-100_000, le=100_000)
    unit: str | None = Field(default=None, max_length=32)
    unit_price_cents: int = Field(default=0, ge=-10_000_000, le=10_000_000)
    line_total_cents: int | None = Field(default=None, ge=-100_000_000, le=100_000_000)
    kind: str = Field(default="item", pattern="^(item|fee|credit)$")
    item_id: str | None = None
    pack_size: float = Field(default=1, gt=0, le=10_000)
    match: str | None = Field(default=None, max_length=40)


class DraftIn(BaseModel):
    supplier_id: str | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    total_cents: int | None = None
    lines: list[DraftLine] | None = Field(default=None, max_length=300)


@router.put("/invoices/{scan_id}")
async def edit_invoice(scan_id: str, body: DraftIn, location: Location = Depends(require_location),
                       session: AsyncSession = Depends(get_session)):
    scan = await _scan(session, location, scan_id)
    changes = body.model_dump(exclude_unset=True)
    if "lines" in changes:
        changes["lines"] = [ln.model_dump() for ln in body.lines or []]
    try:
        await receiving.update_draft(session, location, scan, changes)
    except receiving.ReceivingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return receiving.scan_out(scan, location.currency)


class ConfirmIn(BaseModel):
    purchase_order_id: str | None = None
    allow_duplicate: bool = False


@router.post("/invoices/{scan_id}/confirm")
async def confirm_invoice(scan_id: str, body: ConfirmIn | None = None, location: Location = Depends(require_location),
                          user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    scan = await _scan(session, location, scan_id)
    body = body or ConfirmIn()
    try:
        result = await receiving.confirm(session, location, scan, user.id, purchase_order_id=body.purchase_order_id,
                                         allow_duplicate=body.allow_duplicate)
    except receiving.ReceivingError as exc:
        raise HTTPException(409, str(exc)) from exc
    return result


@router.post("/invoices/{scan_id}/discard")
async def discard_invoice(scan_id: str, location: Location = Depends(require_location),
                          session: AsyncSession = Depends(get_session)):
    scan = await _scan(session, location, scan_id)
    if scan.status == "confirmed":
        raise HTTPException(409, "Already in the receiving log")
    scan.status = "discarded"
    await session.commit()
    return {"ok": True}


@router.post("/invoices/{scan_id}/retry")
async def retry_invoice(scan_id: str, location: Location = Depends(require_location),
                        session: AsyncSession = Depends(get_session)):
    """Read the invoice again (after a failure, or to discard manual edits)."""
    from app.services.jobs import enqueue

    scan = await _scan(session, location, scan_id)
    if scan.status not in ("failed", "review"):
        raise HTTPException(409, f"This invoice is {scan.status}")
    scan.status, scan.error = "processing", None
    await enqueue(session, "read_invoice", {"scan_id": scan.id}, dedupe_key=f"read_invoice:{scan.id}", max_attempts=3)
    await session.commit()
    return receiving.scan_out(scan, location.currency)


@router.get("/receiving-log")
async def receiving_log(start: date | None = None, end: date | None = None, supplier_id: str | None = None,
                        q: str | None = None, item_id: str | None = None, changed_only: bool = False,
                        format: str = Query("json", pattern="^(json|csv)$"),
                        location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    """Every line received, newest first. format=csv downloads it as a spreadsheet."""
    out = await receiving.log_lines(session, location, start=start, end=end, supplier_id=supplier_id, search=q,
                                    item_id=item_id, changed_only=changed_only)
    if format == "csv":
        name = f"receiving-log-{location.name.lower().replace(' ', '-')}.csv"
        return Response(receiving.to_csv(out["lines"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return out


@router.get("/price-changes")
async def get_price_changes(days: int = Query(90, ge=1, le=730), min_pct: float = 0.0,
                            location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    return await receiving.price_changes(session, location, days, min_pct)


@router.get("/price-history")
async def get_price_history(item_id: str | None = None, q: str | None = None,
                            location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    try:
        return await receiving.price_history(session, location, item_id=item_id, search=q)
    except receiving.ReceivingError as exc:
        raise HTTPException(422, str(exc)) from exc
