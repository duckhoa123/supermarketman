"""Turning what the AI read off an invoice into clean lines, recognising products, and comparing prices.
Pure functions, no database."""

import re
from datetime import date
from difflib import SequenceMatcher

LINE_KINDS = ("item", "fee", "credit")
TOTALS_TOLERANCE = 0.02  # lines may differ from the subtotal by 2% (rounding, deposits) before we warn


def normalize(text: str | None) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def product_key(supplier: str | None, description: str, sku: str | None = None) -> str:
    """How the same product is recognised on the next invoice from that supplier: its code if printed,
    otherwise its normalised description."""
    who = normalize(supplier) or "unknown"
    what = f"sku:{normalize(sku)}" if sku and normalize(sku) else normalize(description)
    return f"{who}|{what}"[:400]


def cents(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "")) * 100)
    except ValueError:
        return None


def _num(value, default: float | None = None) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else default
    except ValueError:
        return default


def parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def clean_extraction(raw: dict) -> dict:
    """Normalise the AI's reading of an invoice into header fields, lines in cents, and warnings."""
    lines, warnings = [], []
    for r in raw.get("lines") or []:
        description = str(r.get("description") or "").strip()[:300]
        if not description:
            continue
        qty = _num(r.get("qty"), 1.0) or 1.0
        unit_price = cents(r.get("unit_price"))
        total = cents(r.get("line_total"))
        if unit_price is None and total is not None:
            unit_price = round(total / qty)
        if total is None and unit_price is not None:
            total = round(unit_price * qty)
        kind = r.get("kind") if r.get("kind") in LINE_KINDS else "item"
        if total is not None and total < 0:
            kind = "credit"
        lines.append({
            "description": description, "sku": (str(r.get("sku")).strip()[:100] or None) if r.get("sku") else None,
            "qty": qty, "unit": (str(r.get("unit")).strip().lower()[:32] or None) if r.get("unit") else None,
            "unit_price_cents": unit_price or 0, "line_total_cents": total or 0, "kind": kind,
        })
    out = {
        "supplier_name": (str(raw.get("supplier_name") or "").strip()[:200]) or None,
        "supplier_email": (str(raw.get("supplier_email") or "").strip()[:320]) or None,
        "invoice_number": (str(raw.get("invoice_number") or "").strip()[:100]) or None,
        "invoice_date": parse_date(raw.get("invoice_date")),
        "subtotal_cents": cents(raw.get("subtotal")),
        "tax_cents": cents(raw.get("tax")),
        "total_cents": cents(raw.get("total")),
        "lines": lines,
    }
    if not lines:
        warnings.append("No lines could be read. Retake the photo flat and in good light, or add lines by hand.")
    if not out["supplier_name"]:
        warnings.append("Supplier name not found on the invoice.")
    if not out["invoice_date"]:
        warnings.append("Invoice date not found; today's date will be used.")
    if lines and out["subtotal_cents"]:
        total = sum(ln["line_total_cents"] for ln in lines)
        if abs(total - out["subtotal_cents"]) > abs(out["subtotal_cents"]) * TOTALS_TOLERANCE:
            warnings.append(f"Lines add up to ${total / 100:,.2f} but the invoice subtotal is "
                            f"${out['subtotal_cents'] / 100:,.2f}. Check for a missed or misread line.")
    for w in raw.get("unreadable") or []:
        warnings.append(f"Hard to read: {str(w)[:200]}")
    out["warnings"] = warnings
    return out


STOP_WORDS = {"and", "the", "of", "with", "x", "ea", "each", "pack", "case", "box", "fresh", "pc", "pcs"}


def _words(text: str) -> set[str]:
    """Meaningful words: no sizes or counts (125g, 330ml, 6), no filler."""
    return {w for w in normalize(text).split() if w not in STOP_WORDS and not any(ch.isdigit() for ch in w)}


def match_item(description: str, items: list[tuple[str, str]], threshold: float = 0.6) -> tuple[str, float] | None:
    """Best stock item for an invoice line by name: (item_id, score) or None.

    Scores the closer of: overall text similarity, or the share of words in common (either way round),
    so "Burrata 125g x 6" finds "Burrata & Tomato" and "Peroni Nastro Azzurro 330ml" finds "Peroni"."""
    target = normalize(description)
    line_words = _words(description)
    if not target:
        return None
    best: tuple[str, float] | None = None
    for item_id, name in items:
        n = normalize(name)
        item_words = _words(name)
        if not n:
            continue
        ratio = SequenceMatcher(None, target, n).ratio()
        common = len(line_words & item_words)
        overlap = max(common / max(1, len(item_words)), common / max(1, len(line_words))) if common else 0.0
        score = max(ratio, overlap * 0.9)
        if best is None or score > best[1]:
            best = (item_id, round(score, 2))
    return best if best and best[1] >= threshold else None


def per_stock_unit(unit_price_cents: int, pack_size: float | None) -> float:
    return unit_price_cents / (pack_size or 1)


def price_change_pct(new_cents: int, new_pack: float | None, old_cents: int | None, old_pack: float | None) -> float | None:
    """Change in price per stock unit, so a 24-pack and singles of the same thing compare fairly."""
    if not old_cents or not new_cents:
        return None
    old = per_stock_unit(old_cents, old_pack)
    return round((per_stock_unit(new_cents, new_pack) - old) / old * 100, 1)


def weekly_impact_cents(change_per_unit_cents: float, units_last_8_weeks: float) -> int:
    """What a price change costs (or saves) per week at the usual order volume."""
    return round(change_per_unit_cents * units_last_8_weeks / 8)
