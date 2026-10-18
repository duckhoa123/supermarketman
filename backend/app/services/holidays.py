"""British Columbia statutory holidays and the special days that change restaurant sales.

Pure functions. Statutory holidays follow the BC Employment Standards Act list (Boxing Day and Easter Monday
are not BC statutory holidays). Lunar New Year dates are listed explicitly because they follow the lunar
calendar; extend LUNAR_NEW_YEAR when the table runs out.
"""

from datetime import date, timedelta

LUNAR_NEW_YEAR = {
    2025: date(2025, 1, 29), 2026: date(2026, 2, 17), 2027: date(2027, 2, 6), 2028: date(2028, 1, 26),
    2029: date(2029, 2, 13), 2030: date(2030, 2, 3), 2031: date(2031, 1, 23),
}


def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday, anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th given weekday (0=Mon) of a month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def bc_statutory(year: int) -> dict[date, str]:
    may24 = date(year, 5, 24)
    return {
        date(year, 1, 1): "New Year's Day",
        nth_weekday(year, 2, 0, 3): "Family Day",
        easter(year) - timedelta(days=2): "Good Friday",
        may24 - timedelta(days=may24.weekday()): "Victoria Day",  # Monday on or before May 24
        date(year, 7, 1): "Canada Day",
        nth_weekday(year, 8, 0, 1): "B.C. Day",
        nth_weekday(year, 9, 0, 1): "Labour Day",
        date(year, 9, 30): "National Day for Truth and Reconciliation",
        nth_weekday(year, 10, 0, 2): "Thanksgiving",
        date(year, 11, 11): "Remembrance Day",
        date(year, 12, 25): "Christmas Day",
    }


def special_days(year: int) -> dict[date, str]:
    days = {
        date(year, 2, 14): "Valentine's Day",
        nth_weekday(year, 5, 6, 2): "Mother's Day",
        nth_weekday(year, 6, 6, 3): "Father's Day",
        date(year, 10, 31): "Halloween",
        date(year, 12, 24): "Christmas Eve",
        date(year, 12, 26): "Boxing Day",
        date(year, 12, 31): "New Year's Eve",
    }
    if year in LUNAR_NEW_YEAR:
        days[LUNAR_NEW_YEAR[year]] = "Lunar New Year"
    return days


# Starting assumptions for how a day changes sales vs a normal same weekday, used until the restaurant's
# own history has an example of that day (then the observed effect replaces it).
DEFAULT_EFFECT = {
    "Valentine's Day": 1.25, "Mother's Day": 1.2, "Father's Day": 1.1, "Lunar New Year": 1.15,
    "New Year's Eve": 1.2, "Christmas Eve": 0.85, "Christmas Day": 0.5, "Boxing Day": 0.9, "Halloween": 0.95,
}
STAT_DEFAULT_EFFECT = 0.95


def days_between(start: date, end: date) -> dict[date, tuple[str, str]]:
    """{day: (name, "statutory" | "special")} for every holiday or special day from start to end inclusive."""
    out: dict[date, tuple[str, str]] = {}
    for year in range(start.year, end.year + 1):
        for d, name in special_days(year).items():
            out[d] = (name, "special")
        for d, name in bc_statutory(year).items():
            out[d] = (name, "statutory")  # a statutory holiday wins if both fall on one day
    return {d: v for d, v in sorted(out.items()) if start <= d <= end}


def default_effect(name: str, kind: str) -> float:
    return DEFAULT_EFFECT.get(name, STAT_DEFAULT_EFFECT if kind == "statutory" else 1.0)
