"""Turning what the AI read off an invoice into clean lines, recognising products, and comparing prices.
Pure functions, no database."""

import re
from datetime import date
from difflib import SequenceMatcher

from app.services.inventory import _dimension, convert, norm_unit

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


# --- Packs: "1 case of 40 × 400 g" → what one invoice unit adds to stock -----------------------------------------

_N = r"\d+(?:\.\d+)?"
_MEASURE = r"fl\.? ?oz|kgs?|kilos?|grams?|gm|gr|g|lbs?|#|oz|mls?|ml|ltr|lt|litres?|liters?|l"
_PACK_PATTERNS = (
    ("count_size", re.compile(rf"(?<![a-z0-9.])({_N})\s*[x×*/]\s*({_N})\s*({_MEASURE})(?![a-z])", re.I)),
    ("size_count", re.compile(rf"(?<![a-z0-9.])({_N})\s*({_MEASURE})\s*[x×*]\s*(\d+)(?![a-z0-9])", re.I)),
    ("size", re.compile(rf"(?<![a-z0-9.])({_N})\s*({_MEASURE})(?![a-z])", re.I)),
    ("count", re.compile(r"(?<![a-z0-9.])(\d+)\s*(?:x|×|pk|pack|ct|count|pcs)(?![a-z0-9])", re.I)),
)


def _measure(unit: str | None) -> bool:
    d = _dimension(norm_unit(unit)) if unit else None
    return d is not None and d[0] in ("mass", "volume")


MULTI = {"case", "box", "carton", "ctn", "crate", "flat", "tray", "pack"}  # units that usually hold several


def _count_unknown(inv: str, count: float | None, each_qty: float | None, each_unit: str | None) -> bool:
    """A case of "330ml" with no count printed: the size is one bottle's, and how many are in the case isn't said.
    (A case of "25LB" is the whole case: produce is sold that way.)"""
    if inv not in MULTI or (count or 1) > 1 or not each_qty or not each_unit:
        return False
    d = _dimension(norm_unit(each_unit))
    return d is not None and d[0] == "volume" and (convert(each_qty, each_unit, "ml") or 0) <= 2000


def parse_pack(*texts: str | None) -> dict | None:
    """The pack printed on a line, from the first text that has one: "40X400G" → 40 × 400 g, "6/5LB" → 6 × 5 lb,
    "Burrata 125g x 6" → 6 × 125 g, "RICE 40LBS" → 1 × 40 lb, "Napkins 500ct" → 500."""
    for text in texts:
        for kind, pattern in _PACK_PATTERNS:
            m = pattern.search(text or "")
            if not m:
                continue
            if kind == "count_size":
                return {"pack_count": float(m[1]), "each_qty": float(m[2]), "each_unit": norm_unit(m[3])}
            if kind == "size_count":
                return {"pack_count": float(m[3]), "each_qty": float(m[1]), "each_unit": norm_unit(m[2])}
            if kind == "size":
                return {"pack_count": 1.0, "each_qty": float(m[1]), "each_unit": norm_unit(m[2])}
            if float(m[1]) > 1:
                return {"pack_count": float(m[1]), "each_qty": None, "each_unit": None}
    return None


def clean_name(description: str, sku: str | None = None) -> str:
    """A product name without its item code or pack size, when the AI didn't give one."""
    text = description.replace(sku, " ") if sku else description
    for _, pattern in _PACK_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -/,.")
    return text or description


def per_invoice_unit(inv_unit: str | None, pack_count: float | None, each_qty: float | None,
                     each_unit: str | None, stock_unit: str | None, conversions: list | None = None) -> float | None:
    """How much one invoice unit adds to stock, in the stock unit; None when the pack doesn't say.

    1 case of 40 × 400 g counted in kg → 16; counted in packs → 40; counted in cases → 1.
    10 lb (packed in 1 lb bags) counted in lb → 1 per lb; counted in kg → 0.45."""
    inv, unit = norm_unit(inv_unit), norm_unit(stock_unit)
    count = pack_count or 1
    if not unit:
        return None
    if unit == inv:
        return 1.0
    if _count_unknown(inv, pack_count, each_qty, each_unit):
        return convert(1, inv, unit, conversions) if conversions else None
    if _measure(unit):
        if _measure(inv):  # the invoice already counts by weight or volume; the pack is just how it's bagged
            per = convert(1, inv, unit)
            if per is not None:
                return per
        elif each_qty and each_unit:
            total = convert(count * each_qty, each_unit, unit)
            if total is not None:
                return total
    else:
        if _measure(inv) and each_qty and each_unit:
            per = convert(1, inv, each_unit)
            if per is not None:
                return per / each_qty
        elif count > 1 and inv not in ("each", ""):  # "6 ea of 125g x 6" could be 6 or 36: left to the person
            return count
    if conversions:
        return convert(1, inv, unit, conversions)
    return None


def default_stock_unit(inv_unit: str | None, pack_count: float | None, each_qty: float | None,
                       each_unit: str | None, each_name: str | None = None) -> str:
    """What a new item is counted in: food by weight (kg, lb), bottles and cans one by one, else the invoice unit."""
    inv, size = norm_unit(inv_unit), norm_unit(each_unit) if each_unit else ""
    inner = norm_unit(each_name) if each_name and norm_unit(each_name) != inv else "each"
    if _measure(inv):
        return inv
    if _count_unknown(inv, pack_count, each_qty, each_unit):
        return inner
    if each_qty and _measure(size):
        if _dimension(size)[0] == "mass":
            return {"g": "kg", "mg": "kg"}.get(size, size)
        if (pack_count or 1) > 1:
            return inner
        return {"ml": "l", "cl": "l", "dl": "l"}.get(size, size)
    if (pack_count or 1) > 1:
        return inner
    return inv or "each"


def stock_units(inv_unit: str | None, pack_count: float | None, each_qty: float | None, each_unit: str | None,
                each_name: str | None = None) -> list[str]:
    """Units a new item from this line could be counted in, the suggested one first."""
    first = default_stock_unit(inv_unit, pack_count, each_qty, each_unit, each_name)
    size = norm_unit(each_unit) if each_unit else ""
    family = {"mass": ["kg", "g", "lb", "oz"], "volume": ["l", "ml"]}
    options = [first]
    for u in (norm_unit(inv_unit), size):
        d = _dimension(u) if u else None
        if d and d[0] in family:
            options += family[d[0]]
    if (pack_count or 1) > 1 or each_qty:
        options.append(norm_unit(each_name) if each_name else "each")
    options.append(norm_unit(inv_unit) or "each")
    return list(dict.fromkeys(u for u in options if u))


def new_item_conversions(inv_unit: str | None, per_unit: float | None, stock_unit: str, each_qty: float | None,
                         each_unit: str | None) -> list[dict]:
    """What a new item remembers about its pack: "1 case = 16 kg" for the next invoice, "1 bottle = 750 ml" so a
    recipe can use it by volume."""
    out = []
    inv, unit = norm_unit(inv_unit), norm_unit(stock_unit)
    if per_unit and inv and inv not in (unit, "each") and not (_measure(inv) and _measure(unit)):
        out.append({"qty": 1, "unit": inv, "to_qty": round(per_unit, 6), "to_unit": unit})
    if each_qty and each_unit and not _measure(unit) and _measure(each_unit):
        out.append({"qty": 1, "unit": unit, "to_qty": each_qty, "to_unit": norm_unit(each_unit)})
    return out


def pack_text(pack_count: float | None, each_qty: float | None, each_unit: str | None) -> str:
    count = f"{pack_count:g} × " if pack_count and pack_count != 1 and each_qty else ""
    if each_qty and each_unit:
        return f"{count}{each_qty:g} {each_unit}"
    return f"{pack_count:g}" if pack_count and pack_count > 1 else ""


def _pack_fields(r: dict, description: str) -> dict:
    """The AI's pack reading if it gave one, else what's printed in the packing column or the description."""
    count, size, unit = _num(r.get("pack_count")), _num(r.get("each_size")), r.get("each_unit")
    if size and unit and _measure(str(unit)):
        return {"pack_count": count or 1.0, "each_qty": size, "each_unit": norm_unit(str(unit))}
    if count and count > 1 and not size:
        found = parse_pack(r.get("packing"), description)
        if found and found["each_qty"] and found["pack_count"] == count:
            return found
        return {"pack_count": count, "each_qty": None, "each_unit": None}
    return parse_pack(r.get("packing"), description) or {"pack_count": 1.0, "each_qty": None, "each_unit": None}


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
        sku = (str(r.get("sku")).strip()[:100] or None) if r.get("sku") else None
        name = str(r.get("name") or "").strip()[:300] or clean_name(description, sku)
        each_name = str(r.get("each_name") or "").strip().lower()[:32] or None
        lines.append({
            "description": description, "sku": sku, "name": name,
            "qty": qty, "unit": (str(r.get("unit")).strip().lower()[:32] or None) if r.get("unit") else None,
            "unit_price_cents": unit_price or 0, "line_total_cents": total or 0, "kind": kind,
            **_pack_fields(r, description), "each_name": each_name,
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
    line_words = _words(description)  # sizes and counts are left out, so "Rice 40LBS" still finds "Rice"
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
