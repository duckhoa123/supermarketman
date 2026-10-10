"""Pure functions that turn Clover REST/webhook JSON into normalized shapes.

Kept free of HTTP code so they can be unit tested against recorded payloads.

Clover conventions (docs.clover.com):
- Money is integer cents.
- Timestamps are Unix epoch milliseconds.
- Line item `unitQty` has a scaling factor of 1000 and applies to PER_UNIT items; otherwise each line is
  one unit (Clover represents "2 x Burger" as two line items).
- Webhook `objectId` is "<event key>:<object id>", e.g. "O:GHIVJT2ABCRSC" for an order.
"""

import hmac
import re
from datetime import datetime, timezone

from app.integrations.pos.base import NormalizedItem, NormalizedLine, NormalizedOrder, WebhookChange

EVENT_KEYS = {
    "O": "order",
    "I": "item",
    "IC": "category",
    "IG": "modifier_group",
    "IM": "modifier",
    "P": "payment",
    "C": "customer",
    "E": "employee",
    "M": "merchant",
    "A": "app",
    "CA": "cash_adjustment",
    "SH": "service_hour",
}

_TABLE_TITLE = re.compile(r"^(table|tbl|t)\s*#?\s*\d{1,4}[a-z]?$", re.IGNORECASE)


def _ms(value) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _elements(obj) -> list[dict]:
    """Clover wraps expanded collections as {"elements": [...]}."""
    if isinstance(obj, dict):
        return obj.get("elements") or []
    if isinstance(obj, list):
        return obj
    return []


def normalize_item(raw: dict) -> NormalizedItem:
    categories = _elements(raw.get("categories"))
    stock = raw.get("itemStock") or {}
    qty = stock.get("quantity", stock.get("stockCount"))
    if qty is None:
        qty = raw.get("stockCount")
    return NormalizedItem(
        external_id=raw["id"],
        name=raw.get("name") or "(unnamed item)",
        category=categories[0].get("name") if categories else None,
        price_cents=int(raw.get("price") or 0),
        cost_cents=int(raw["cost"]) if raw.get("cost") is not None else None,
        stock_qty=float(qty) if qty is not None else None,
        unit=raw.get("unitName"),
        active=not raw.get("hidden", False) and not raw.get("deleted", False) and raw.get("available", True),
    )


def _line(raw: dict) -> NormalizedLine | None:
    if raw.get("refunded") or raw.get("exchanged"):
        return None
    price = int(raw.get("price") or 0)
    unit_qty = raw.get("unitQty")
    qty = (int(unit_qty) / 1000) if unit_qty else 1.0
    modifiers = sum(int(m.get("amount") or 0) for m in _elements(raw.get("modifications")))
    total = round(price * qty) + modifiers
    item = raw.get("item") or {}
    return NormalizedLine(
        name=raw.get("name") or "(item)",
        qty=qty,
        unit_price_cents=price,
        total_cents=total,
        item_external_id=item.get("id"),
    )


def _status(raw: dict) -> str:
    payment_state = (raw.get("paymentState") or "").upper()
    if raw.get("deleted") or (raw.get("state") or "").lower() == "deleted":
        return "void"
    if payment_state in ("REFUNDED",):
        return "refunded"
    if payment_state in ("PAID", "LOCKED") or (raw.get("state") or "").lower() == "locked":
        return "paid"
    return "open"


def normalize_order(raw: dict) -> NormalizedOrder:
    payments = [p for p in _elements(raw.get("payments")) if (p.get("result") or "SUCCESS") == "SUCCESS"]
    lines = [ln for ln in (_line(li) for li in _elements(raw.get("lineItems"))) if ln is not None]
    employee = raw.get("employee") or {}
    opened = _ms(raw.get("clientCreatedTime") or raw.get("createdTime")) or datetime.now(timezone.utc)
    status = _status(raw)
    title = (raw.get("title") or "").strip()
    return NormalizedOrder(
        external_id=raw["id"],
        status=status,
        total_cents=int(raw.get("total") or 0),
        tax_cents=sum(int(p.get("taxAmount") or 0) for p in payments),
        tip_cents=sum(int(p.get("tipAmount") or 0) for p in payments),
        # Clover has no universal table field; dining setups usually put the table in the order title.
        table_name=title if _TABLE_TITLE.match(title) else None,
        employee_name=employee.get("name"),
        opened_at=opened,
        closed_at=_ms(raw.get("modifiedTime")) if status in ("paid", "refunded", "void") else None,
        lines=lines,
    )


def parse_webhook(body: dict) -> list[WebhookChange]:
    changes: list[WebhookChange] = []
    for merchant_id, updates in (body.get("merchants") or {}).items():
        for upd in updates or []:
            object_id = upd.get("objectId") or ""
            key, _, oid = object_id.partition(":")
            if not oid:
                continue
            changes.append(
                WebhookChange(
                    merchant_id=merchant_id,
                    object_type=EVENT_KEYS.get(key, "other"),
                    object_id=oid,
                    change=(upd.get("type") or "UPDATE").upper(),
                    ts_ms=upd.get("ts"),
                )
            )
    return changes


def verify_auth_header(received: str | None, expected: str) -> bool:
    """Clover sends the app's auth code in X-Clover-Auth; compare in constant time."""
    if not received or not expected:
        return False
    return hmac.compare_digest(received.strip(), expected.strip())
