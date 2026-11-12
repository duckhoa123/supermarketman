"""Import a restaurant's own data from files exported from its POS dashboard (Clover's Items and Orders exports),
for restaurants that don't connect the POS live.

Exports differ by POS and change over time, so nothing here depends on an exact layout: the header row is found
by matching known column names (in any sheet, below any title rows), the owner checks the guessed columns in a
preview, and the import uses the columns they confirmed. Re-importing the same file updates rows instead of
duplicating them (orders and items keep their POS ids), and a later live Clover connection matches the same ids.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Location, Order, OrderLine
from app.services.sync import rebuild_rollups

PROVIDER = "clover"
MAX_BYTES = 15 * 1024 * 1024
MAX_ROWS = 100_000
HEADER_SCAN = 20

# field -> (label, required, header names it goes by; compared without case, spaces or punctuation)
FIELDS: dict[str, dict[str, tuple[str, bool, list[str]]]] = {
    "items": {
        "external_id": ("POS item id", False, ["clover id", "item id", "id", "itemid", "uuid"]),
        "name": ("Name", True, ["name", "item name", "item", "product", "product name", "title"]),
        "price": ("Price", True, ["price", "unit price", "item price", "selling price"]),
        "cost": ("Cost", False, ["cost", "unit cost", "item cost"]),
        "category": ("Category", False, ["categories", "category", "category name", "item category", "group"]),
        "stock": ("In stock", False, ["quantity", "stock", "stock count", "in stock", "qty", "stock quantity",
                                     "item stock", "quantity in stock", "on hand"]),
        "unit": ("Unit", False, ["price unit", "unit", "unit name", "uom"]),
        "sku": ("SKU / code", False, ["sku", "product code", "code", "item code", "barcode"]),
        "hidden": ("Hidden", False, ["hidden", "is hidden", "inactive", "deleted"]),
    },
    "orders": {
        "order_id": ("Order id", False, ["order id", "orderid", "order number", "order no", "order", "order #",
                                         "receipt number", "invoice number", "id"]),
        "date": ("Date and time", True, ["order date", "date", "order time", "created", "created time",
                                         "order created", "created at", "date time", "datetime", "time",
                                         "transaction date", "payment date", "opened", "when", "sold at",
                                         "closed", "paid at", "order placed"]),
        "total": ("Order total", False, ["order total", "total", "amount", "order amount", "total amount",
                                         "grand total", "net total", "sale amount"]),
        "tax": ("Tax", False, ["tax", "tax amount", "taxes", "order tax", "total tax"]),
        "tip": ("Tip", False, ["tip", "tips", "tip amount", "gratuity"]),
        "status": ("Status", False, ["payment state", "order payment state", "payment status", "state", "status",
                                     "order state"]),
        "employee": ("Staff", False, ["employee", "order employee name", "employee name", "server", "cashier",
                                      "order employee", "staff"]),
        "item_name": ("Item", False, ["item name", "item", "line item", "line item name", "product", "product name",
                                      "items"]),
        "qty": ("Item quantity", False, ["quantity", "qty", "item quantity", "count"]),
        "item_price": ("Item price", False, ["item price", "price", "unit price"]),
        "item_total": ("Item total", False, ["item total", "line total", "item revenue", "revenue", "item amount",
                                             "line item total", "item sales"]),
    },
}
KIND_LABEL = {"items": "menu items", "orders": "orders"}


class PosImportError(ValueError):
    pass


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9#]", "", str(text or "").lower())


# --- Reading the file -----------------------------------------------------------------------------------------------


def read_sheets(name: str | None, data: bytes) -> list[tuple[str, list[list]]]:
    """[(sheet name, rows)] from an .xlsx workbook or a CSV/TSV file."""
    if not data:
        raise PosImportError("The file is empty")
    if len(data) > MAX_BYTES:
        raise PosImportError("Files must be under 15 MB: export a shorter date range")
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise PosImportError("This is an old-style Excel file (.xls). Open it and save it as .xlsx or CSV, then "
                           "upload that.")
    if data[:2] == b"PK":
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - not a workbook after all
            raise PosImportError("This file couldn't be opened as a spreadsheet") from exc
        out = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > MAX_ROWS:
                    break
            out.append((ws.title, rows))
        return out
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    return [(name or "file", rows[:MAX_ROWS + 1])]


@dataclass
class Table:
    kind: str
    sheet: str
    header_row: int
    columns: list[str]
    rows: list[list]
    mapping: dict[str, int | None]


def _guess(columns: list[str], kind: str) -> dict[str, int | None]:
    normed = [_norm(c) for c in columns]
    used: set[int] = set()
    mapping: dict[str, int | None] = {}
    for key, (_, _, names) in FIELDS[kind].items():
        idx = None
        for want in (_norm(n) for n in names):
            for i, h in enumerate(normed):
                if i not in used and h == want:
                    idx = i
                    break
            if idx is not None:
                break
        mapping[key] = idx
        if idx is not None:
            used.add(idx)
    return mapping


def _fits(kind: str, m: dict[str, int | None]) -> int:
    """How well a header row fits a kind (0: it doesn't)."""
    found = sum(1 for v in m.values() if v is not None)
    if kind == "items":
        ok = m["name"] is not None and m["price"] is not None
    else:
        ok = m["date"] is not None and (m["total"] is not None or m["item_total"] is not None
                                       or m["item_price"] is not None)
    return found if ok else 0


def detect(sheets: list[tuple[str, list[list]]], kind: str | None = None) -> Table:
    best: tuple[int, Table] | None = None
    for sheet, rows in sheets:
        for r in range(min(HEADER_SCAN, len(rows))):
            columns = [str(c).strip() if c is not None else "" for c in rows[r]]
            if sum(1 for c in columns if c) < 2:
                continue
            for k in ([kind] if kind else ["orders", "items"]):
                m = _guess(columns, k)
                score = _fits(k, m)
                # An orders file has dates; don't read a menu list with a "date added" column as orders.
                if k == "items" and _guess(columns, "orders")["date"] is not None and kind is None:
                    score = min(score, 1)
                if score and (best is None or score > best[0]):
                    body = [row for row in rows[r + 1:] if any(c not in (None, "") for c in row)]
                    best = (score, Table(k, sheet, r, columns, body, m))
    if best is None:
        raise PosImportError("This doesn't look like an items or orders export: no row of column names with a name "
                           "and price (items), or a date and total (orders), was found.")
    return best[1]


# --- Values -----------------------------------------------------------------------------------------------------------


def cell(row: list, idx: int | None):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    return v.strip() if isinstance(v, str) else v


def money(v) -> int | None:
    """'$1,234.50' -> 123450; '(5.00)' -> -500. Numbers are dollars."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v) * 100)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^0-9.]", "", s.replace(",", ""))
    if not s or s == ".":
        return None
    try:
        cents = round(float(s) * 100)
    except ValueError:
        return None
    return -cents if neg else cents


def number(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def when(v, tz: ZoneInfo) -> datetime | None:
    """A local date/time from the file, as UTC."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        d = datetime.combine(v, time(12, 0))
    else:
        from dateutil import parser

        s = re.sub(r"\s+", " ", str(v)).strip()
        s = re.sub(r"\b(PST|PDT|EST|EDT|CST|CDT|MST|MDT|AEST|AEDT|UTC|GMT)\b", "", s).strip()
        try:
            d = parser.parse(s, dayfirst=False, fuzzy=True)
        except (ValueError, OverflowError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.astimezone(ZoneInfo("UTC"))


def truthy(v) -> bool:
    return str(v or "").strip().lower() in ("yes", "y", "true", "1", "hidden", "x")


def status_of(v, total: int) -> str:
    s = str(v or "").strip().lower()
    if any(w in s for w in ("refund",)):
        return "refunded"
    if any(w in s for w in ("void", "delet", "cancel")):
        return "void"
    if any(w in s for w in ("open", "unpaid", "partial")):
        return "open"
    if s or total > 0:
        return "paid"
    return "open"


# --- Preview and import ----------------------------------------------------------------------------------------------


def _sample(t: Table, tz: ZoneInfo, n: int = 8) -> list[dict]:
    out = []
    for row in t.rows[:n]:
        rec = {}
        for key, idx in t.mapping.items():
            v = cell(row, idx)
            if v is None:
                continue
            if key == "date":
                d = when(v, tz)
                rec[key] = d.astimezone(tz).strftime("%Y-%m-%d %H:%M") if d else f"? {v}"
            elif isinstance(v, datetime):
                rec[key] = v.isoformat(sep=" ", timespec="minutes")
            else:
                rec[key] = str(v)[:80]
        out.append(rec)
    return out


def preview(location: Location, name: str | None, data: bytes, kind: str | None = None) -> dict:
    sheets = read_sheets(name, data)
    rep = detect_report(sheets) if kind in (None, "sales_report") else None
    if rep is not None:
        return report_preview(location, rep)
    t = detect(sheets, None if kind == "sales_report" else kind)
    tz = ZoneInfo(location.timezone)
    warnings = []
    if t.kind == "orders" and t.mapping["order_id"] is None:
        warnings.append("No order id column: each row is counted as its own order, and importing the same file "
                        "twice would count it twice.")
    if t.kind == "orders" and t.mapping["item_name"] is None:
        warnings.append("No item column: sales totals are imported, but not which dishes sold.")
    if t.kind == "items" and t.mapping["external_id"] is None:
        warnings.append("No item id column: items are matched to existing ones by name.")
    return {
        "kind": t.kind, "label": KIND_LABEL[t.kind], "sheet": t.sheet, "header_row": t.header_row + 1,
        "rows": len(t.rows), "columns": t.columns,
        "fields": [{"key": k, "label": label, "required": req} for k, (label, req, _) in FIELDS[t.kind].items()],
        "mapping": t.mapping, "sample": _sample(t, tz), "warnings": warnings,
    }


def _use_mapping(t: Table, mapping: dict | None) -> Table:
    if mapping:
        fields = FIELDS[t.kind]
        clean: dict[str, int | None] = {}
        for k in fields:
            v = mapping.get(k, t.mapping.get(k))
            clean[k] = v if isinstance(v, int) and 0 <= v < len(t.columns) else None
        t.mapping = clean
    for k, (label, req, _) in FIELDS[t.kind].items():
        if req and t.mapping.get(k) is None:
            raise PosImportError(f"Choose which column holds “{label}”")
    return t


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


async def import_items(session: AsyncSession, location: Location, t: Table) -> dict:
    m = t.mapping
    existing = list(await session.scalars(select(Item).where(Item.location_id == location.id)))
    by_ext = {(i.provider, i.external_id): i for i in existing}
    by_name = {i.name.strip().lower(): i for i in existing if i.provider != "demo"}
    added = updated = skipped = 0
    for row in t.rows:
        name = str(cell(row, m["name"]) or "").strip()
        price = money(cell(row, m["price"]))
        if not name:
            skipped += 1
            continue
        ext = str(cell(row, m["external_id"]) or "").strip() or f"import-{_slug(name)}"
        item = by_ext.get((PROVIDER, ext)) or by_name.get(name.lower())
        if item is None:
            item = Item(location_id=location.id, provider=PROVIDER, external_id=ext[:100], name=name[:300])
            session.add(item)
            by_ext[(PROVIDER, ext)] = item
            by_name[name.lower()] = item
            added += 1
        else:
            updated += 1
        item.name = name[:300]
        if price is not None:
            item.price_cents = max(0, price)
        cost = money(cell(row, m["cost"]))
        if cost is not None:
            item.cost_cents = max(0, cost)
        cat = str(cell(row, m["category"]) or "").strip()
        if cat:
            item.category = re.split(r"[,;|]", cat)[0].strip()[:200]
        stock = number(cell(row, m["stock"]))
        if m["stock"] is not None and stock is not None:
            item.stock_qty = stock
        unit = str(cell(row, m["unit"]) or "").strip()
        if unit:
            item.unit = unit[:32]
        if m["hidden"] is not None:
            item.active = not truthy(cell(row, m["hidden"]))
        else:
            item.active = True
    await session.commit()
    return {"kind": "items", "added": added, "updated": updated, "skipped": skipped}


async def import_orders(session: AsyncSession, location: Location, t: Table) -> dict:
    m = t.mapping
    tz = ZoneInfo(location.timezone)
    groups: dict[str, list[list]] = {}
    order_of: list[str] = []
    skipped = 0
    for n, row in enumerate(t.rows):
        oid = str(cell(row, m["order_id"]) or "").strip() if m["order_id"] is not None else ""
        if not oid:
            under = order_of and m["item_name"] is not None and cell(row, m["item_name"])
            if m["order_id"] is not None and under and when(cell(row, m["date"]), tz) is None:
                oid = order_of[-1]  # a line-item row under its order (the order's cells left blank)
            else:
                stamp = f"{cell(row, m['date'])}|{cell(row, m['total'])}|{n}"
                oid = "import-" + hashlib.sha1(stamp.encode()).hexdigest()[:16]
        if oid not in groups:
            groups[oid] = []
            order_of.append(oid)
        groups[oid].append(row)
    categories = {i.name.strip().lower(): i.category for i in await session.scalars(
        select(Item).where(Item.location_id == location.id))}
    item_ids = {i.name.strip().lower(): i.external_id for i in await session.scalars(
        select(Item).where(Item.location_id == location.id, Item.provider == PROVIDER))}
    existing: dict[str, Order] = {}
    ids = list(groups)
    for i in range(0, len(ids), 500):
        for o in await session.scalars(select(Order).where(Order.location_id == location.id,
                                                           Order.provider == PROVIDER,
                                                           Order.external_id.in_(ids[i:i + 500]))):
            existing[o.external_id] = o
    added = updated = 0
    days: set[date] = set()
    for oid in order_of:
        rows = groups[oid]
        opened = next((d for d in (when(cell(r, m["date"]), tz) for r in rows) if d), None)
        if opened is None:
            skipped += 1
            continue
        lines = []
        for r in rows:
            name = str(cell(r, m["item_name"]) or "").strip() if m["item_name"] is not None else ""
            if not name:
                continue
            qty = number(cell(r, m["qty"])) or 1.0
            unit_price = money(cell(r, m["item_price"]))
            line_total = money(cell(r, m["item_total"]))
            if line_total is None:
                line_total = round((unit_price or 0) * qty)
            if unit_price is None:
                unit_price = round(line_total / qty) if qty else line_total
            lines.append(OrderLine(item_external_id=item_ids.get(name.lower()), name=name[:300],
                                   category=categories.get(name.lower()), qty=qty, unit_price_cents=unit_price,
                                   total_cents=line_total))
        o = existing.get(oid)
        fresh = o is None
        if fresh:
            o = Order(location_id=location.id, provider=PROVIDER, external_id=oid[:100], total_cents=0, tax_cents=0,
                      tip_cents=0)
            session.add(o)
            existing[oid] = o
            added += 1
        else:
            days.add(o.opened_at.astimezone(tz).date())
            updated += 1

        def first(key, conv=money, rows=rows):
            return next((v for v in (conv(cell(r, m[key])) for r in rows) if v not in (None, "")), None)

        # Columns the file doesn't have leave what's already known (e.g. a line-items file keeps the totals).
        total = first("total")
        if total is not None:
            o.total_cents = total
        elif fresh or m["item_name"] is not None and not o.total_cents:
            o.total_cents = sum(ln.total_cents for ln in lines)
        for key, attr in (("tax", "tax_cents"), ("tip", "tip_cents")):
            v = first(key)
            if v is not None:
                setattr(o, attr, v)
        if m["status"] is not None or fresh:
            o.status = status_of(first("status", lambda v: v), o.total_cents)
        employee = first("employee", lambda v: str(v) if v else None)
        if employee:
            o.employee_name = employee[:200]
        o.opened_at = opened
        o.closed_at = opened if o.status != "open" else None
        if lines or m["item_name"] is not None:
            o.lines = lines
        days.add(opened.astimezone(tz).date())
    await session.flush()
    await rebuild_rollups(session, location, days)
    await session.commit()
    first, last = (min(days), max(days)) if days else (None, None)
    return {"kind": "orders", "added": added, "updated": updated, "skipped": skipped,
            "from": first.isoformat() if first else None, "to": last.isoformat() if last else None}


async def run(session: AsyncSession, location: Location, name: str | None, data: bytes, kind: str | None,
              mapping: dict | None, day: date | None = None) -> dict:
    sheets = read_sheets(name, data)
    rep = detect_report(sheets) if kind in (None, "sales_report") else None
    if rep is not None:
        if len(set(rep.dates)) > 1 and day is None:
            raise PosImportError("This report covers more than one day: export it one day at a time")
        day = day or (rep.dates[0] if rep.dates else None)
        if day is None:
            raise PosImportError("Choose which day this report is for")
        return await import_report(session, location, rep, day)
    t = _use_mapping(detect(sheets, None if kind == "sales_report" else kind), mapping)
    if t.kind == "items":
        return await import_items(session, location, t)
    return await import_orders(session, location, t)


# --- Clover's Sales Report (Reports → Sales → Export): totals by hour, not single orders -------------------------------

HOUR = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*$", re.I)
DATE_TEXT = re.compile(r"[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")


def _hour(label) -> int | None:
    m = HOUR.match(str(label or ""))
    if not m:
        return None
    h = int(m.group(1)) % 12
    return h + 12 if m.group(3).lower() == "p" else h


@dataclass
class Report:
    sheet: str
    hours: dict[int, int]  # column index -> hour of the day
    total_col: int | None
    lines: dict[str, list]  # row label (normalised) -> the row
    orders: int | None
    dates: list[date]


def detect_report(sheets: list[tuple[str, list[list]]]) -> Report | None:
    for sheet, rows in sheets:
        head = next((i for i, r in enumerate(rows[:60]) if sum(1 for c in r if _hour(c) is not None) >= 12), None)
        if head is None:
            continue
        hours = {i: h for i, c in enumerate(rows[head]) if (h := _hour(c)) is not None}
        total_col = next((i for i, c in enumerate(rows[head]) if _norm(c) == "total"), None)
        lines: dict[str, list] = {}
        for r in rows[head + 1:]:
            label = _norm(r[0] if r else "")
            if not label or label in lines:
                if lines and not label:
                    break  # the end of the sales block
                continue
            lines[label] = r
        if not any(k.startswith("netsales") or k.startswith("grosssales") for k in lines):
            continue
        orders, dates = None, []
        for r in rows[:head]:
            text = " ".join(str(c) for c in r if c not in (None, ""))
            m = re.search(r"from\s+([\d,]+)\s+orders?", text, re.I)
            if m:
                orders = int(m.group(1).replace(",", ""))
            if re.match(r"\s*requested", text, re.I):
                continue  # when the report was downloaded, not what it covers
            for d in DATE_TEXT.findall(text):
                parsed = when(d, ZoneInfo("UTC"))
                if parsed:
                    dates.append(parsed.date())
        return Report(sheet, hours, total_col, lines, orders, sorted(set(dates)))
    return None


def _row(rep: Report, *starts: str) -> list | None:
    for s in starts:
        for k, r in rep.lines.items():
            if k.startswith(s):
                return r
    return None


def report_totals(rep: Report) -> dict:
    """Per hour: what customers paid for food and drink including tax (Pass's "sales"), plus tax and tips."""
    net = _row(rep, "netsales") or _row(rep, "grosssales")
    taxes = _row(rep, "taxescollected", "taxcollected", "taxes", "tax")
    tips = _row(rep, "tips", "tip")
    by_hour: dict[str, int] = {}
    for col, h in rep.hours.items():
        cents = (money(cell(net, col)) or 0) + (money(cell(taxes, col)) or 0 if taxes else 0)
        if cents:
            by_hour[str(h)] = by_hour.get(str(h), 0) + cents

    def total(row):
        if row is None:
            return 0
        if rep.total_col is not None and money(cell(row, rep.total_col)) is not None:
            return money(cell(row, rep.total_col))
        return sum(money(cell(row, c)) or 0 for c in rep.hours)

    tax = total(taxes)
    return {"sales_cents": total(net) + tax, "tax_cents": tax, "tip_cents": total(tips), "by_hour": by_hour,
            "orders": rep.orders}


def report_preview(location: Location, rep: Report) -> dict:
    t = report_totals(rep)
    one_day = rep.dates[0] if len(rep.dates) == 1 or (len(rep.dates) == 2 and rep.dates[0] == rep.dates[1]) else None
    warnings = ["This report has totals by hour, not single orders: Pass gets the day's sales, tax, tips and busiest "
                "hours, but not which dishes sold. The Orders export has those."]
    if len(set(rep.dates)) > 1:
        warnings.append("It covers more than one day. Export the Sales report one day at a time so each day's "
                        "sales land on the right date.")
    return {"kind": "sales_report", "label": "a sales report", "sheet": rep.sheet, "rows": len(rep.hours),
            "day": one_day.isoformat() if one_day else None, "needs_day": one_day is None,
            "multi_day": len(set(rep.dates)) > 1,
            "totals": {"sales": t["sales_cents"], "tax": t["tax_cents"], "tips": t["tip_cents"], "orders": t["orders"],
                       "busiest_hour": max(t["by_hour"], key=t["by_hour"].get) if t["by_hour"] else None},
            "warnings": warnings}


async def import_report(session: AsyncSession, location: Location, rep: Report, day: date) -> dict:
    from app.models import DailySales

    t = report_totals(rep)
    tz = ZoneInfo(location.timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    has_orders = await session.scalar(select(Order.provider).where(
        Order.location_id == location.id, Order.opened_at >= start,
        Order.opened_at < datetime.combine(day, time.max, tzinfo=tz)).limit(1))
    if has_orders == "demo":
        raise PosImportError(f"{day.isoformat()} still has the demo restaurant's sample sales. Remove the demo data "
                             "(below, in Settings) first, then import this report again.")
    if has_orders:
        raise PosImportError(f"Pass already has single orders for {day.isoformat()}, which are more detailed than "
                             "this report, so it wasn't imported for that day.")
    row = await session.get(DailySales, (location.id, day))
    if row is None:
        row = DailySales(location_id=location.id, day=day)
        session.add(row)
    row.gross_cents, row.tax_cents, row.tip_cents = t["sales_cents"], t["tax_cents"], t["tip_cents"]
    row.orders_count, row.guests, row.by_hour = t["orders"] or 0, 0, t["by_hour"]
    await session.commit()
    return {"kind": "sales_report", "day": day.isoformat(), "sales_cents": t["sales_cents"], "orders": t["orders"]}
