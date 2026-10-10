"""Money and time helpers. Pure functions, no third-party imports."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def fmt_money(cents: int | None, currency: str = "AUD") -> str:
    if cents is None:
        return "-"
    symbol = {"AUD": "$", "USD": "$", "CAD": "$", "NZD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    sign = "-" if cents < 0 else ""
    return f"{sign}{symbol}{abs(cents) / 100:,.2f}"


def ms_to_dt(ms: int | None) -> datetime | None:
    """Clover timestamps are Unix epoch milliseconds."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def local_day(dt: datetime, tz: str) -> date:
    return dt.astimezone(ZoneInfo(tz)).date()


def local_hour(dt: datetime, tz: str) -> int:
    return dt.astimezone(ZoneInfo(tz)).hour


def pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


# --- Period parsing used by the agent tools --------------------------------------------------------

PERIODS = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "last_7_days",
    "this_month",
    "last_month",
    "last_30_days",
)


def resolve_period(period: str, today: date) -> tuple[date, date]:
    """Return an inclusive (start, end) date range for a named period, or 'YYYY-MM-DD..YYYY-MM-DD'."""
    if ".." in period:
        a, b = period.split("..", 1)
        start, end = date.fromisoformat(a.strip()), date.fromisoformat(b.strip())
        if end < start:
            start, end = end, start
        return start, end
    if period == "today":
        return today, today
    if period == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if period == "this_week":  # Monday-based weeks
        start = today - timedelta(days=today.weekday())
        return start, today
    if period == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if period == "last_30_days":
        return today - timedelta(days=29), today
    raise ValueError(f"Unknown period '{period}'. Use one of {', '.join(PERIODS)} or YYYY-MM-DD..YYYY-MM-DD")


def previous_period(start: date, end: date, mode: str = "previous") -> tuple[date, date]:
    """The comparison range: the period right before ('previous') or the same days a week/year earlier."""
    length = (end - start).days + 1
    if mode == "previous":
        return start - timedelta(days=length), start - timedelta(days=1)
    if mode == "same_weekday_last_week":
        return start - timedelta(days=7), end - timedelta(days=7)
    if mode == "last_year":
        return start - timedelta(days=364), end - timedelta(days=364)  # 52 weeks: keeps weekdays aligned
    raise ValueError(f"Unknown comparison '{mode}'")
