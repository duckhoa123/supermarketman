"""Pure roll-up computation: orders -> per-day and per-item-per-day totals in the restaurant's time zone."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

COUNTED_STATUSES = ("paid", "open")  # refunded and void orders are excluded from sales


@dataclass
class OrderRow:
    status: str
    opened_at: datetime
    total_cents: int
    tax_cents: int = 0
    tip_cents: int = 0
    guests: int | None = None
    lines: list[tuple[str, str | None, float, int]] = field(default_factory=list)  # name, category, qty, cents


@dataclass
class DayTotals:
    gross_cents: int = 0
    tax_cents: int = 0
    tip_cents: int = 0
    orders_count: int = 0
    guests: int = 0
    by_hour: dict[str, int] = field(default_factory=dict)


def compute_rollups(
    orders: list[OrderRow], tz: str
) -> tuple[dict[date, DayTotals], dict[tuple[date, str], tuple[str | None, float, int]]]:
    zone = ZoneInfo(tz)
    days: dict[date, DayTotals] = defaultdict(DayTotals)
    items: dict[tuple[date, str], list] = {}
    for o in orders:
        if o.status not in COUNTED_STATUSES:
            continue
        local = o.opened_at.astimezone(zone)
        d = days[local.date()]
        d.gross_cents += o.total_cents
        d.tax_cents += o.tax_cents
        d.tip_cents += o.tip_cents
        d.orders_count += 1
        d.guests += o.guests or 0
        hour = str(local.hour)
        d.by_hour[hour] = d.by_hour.get(hour, 0) + o.total_cents
        for name, category, qty, cents in o.lines:
            key = (local.date(), name)
            if key not in items:
                items[key] = [category, 0.0, 0]
            items[key][1] += qty
            items[key][2] += cents
    return dict(days), {k: (v[0], v[1], v[2]) for k, v in items.items()}
