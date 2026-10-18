"""Sales forecast from the restaurant's own history, holidays and the weather. Pure functions.

Method (simple on purpose, so every number can be explained to an owner):
1. Baseline: average of the last 6 matching weekdays that were not holidays or special days.
2. Trend: last 14 days vs the 8 weeks before, limited to +/-10%.
3. Holiday or special day: the effect seen on the same day in this restaurant's history; until there is
   one, a starting assumption (holidays.DEFAULT_EFFECT).
4. Weather: rain, snow, cold and heat adjust dine-in sales by a few percent (assumptions, flagged as such).
The range is the baseline's own spread (lowest to highest of those 6 weekdays), scaled the same way.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.services import holidays

BASELINE_WEEKS = 6
TREND_LIMIT = 0.10


@dataclass
class Weather:
    temp_max_c: float | None = None
    precip_mm: float | None = None
    code: int | None = None


def _same_weekday_history(history: dict[date, int], day: date, special: set[date], weeks: int) -> list[int]:
    values = []
    d = day - timedelta(days=7)
    # Look back up to twice as far so holidays and closed days can be skipped.
    for _ in range(weeks * 2):
        if d in history and d not in special and history[d] > 0:
            values.append(history[d])
            if len(values) == weeks:
                break
        d -= timedelta(days=7)
    return values


def _trend(history: dict[date, int], today: date) -> float:
    recent = [history.get(today - timedelta(days=i), 0) for i in range(1, 15)]
    before = [history.get(today - timedelta(days=i), 0) for i in range(15, 71)]
    if sum(1 for v in recent if v) < 10 or sum(1 for v in before if v) < 35:
        return 1.0
    ratio = (sum(recent) / 14) / (sum(before) / 56) if sum(before) else 1.0
    return max(1 - TREND_LIMIT, min(1 + TREND_LIMIT, ratio))


def weather_effect(w: Weather | None) -> tuple[float, str] | None:
    if w is None:
        return None
    if w.code is not None and (71 <= w.code <= 77 or w.code in (85, 86)):
        return 0.8, "snow"
    if w.precip_mm is not None and w.precip_mm >= 10:
        return 0.9, f"heavy rain ({w.precip_mm:.0f} mm)"
    if w.precip_mm is not None and w.precip_mm >= 3:
        return 0.95, f"rain ({w.precip_mm:.0f} mm)"
    if w.temp_max_c is not None and w.temp_max_c >= 25:
        return 1.05, f"warm ({w.temp_max_c:.0f}°C), patio weather"
    if w.temp_max_c is not None and w.temp_max_c <= 0:
        return 0.9, f"cold ({w.temp_max_c:.0f}°C)"
    return None


def learned_effect(history: dict[date, int], name: str, special_all: dict[date, tuple[str, str]]) -> float | None:
    """Average ratio of sales on past occurrences of this named day to the normal same weekday."""
    special = set(special_all)
    ratios = []
    for d, (n, _kind) in special_all.items():
        if n != name or d not in history:
            continue
        base = _same_weekday_history(history, d, special, BASELINE_WEEKS)
        if len(base) >= 3:
            ratios.append(history[d] / (sum(base) / len(base)))
    return round(sum(ratios) / len(ratios), 3) if ratios else None


def forecast(history: dict[date, int], today: date, days: int = 7,
             weather: dict[date, Weather] | None = None) -> list[dict]:
    """Forecast gross sales (cents) for today+1 .. today+days. `history` maps past local days to gross cents."""
    weather = weather or {}
    first = min(history) if history else today
    special_all = holidays.days_between(first - timedelta(days=1), today + timedelta(days=days))
    special = set(special_all)
    trend = _trend(history, today)
    out = []
    for i in range(1, days + 1):
        day = today + timedelta(days=i)
        base = _same_weekday_history(history, day, special, BASELINE_WEEKS)
        row: dict = {"day": day.isoformat(), "weekday": day.strftime("%a"), "factors": []}
        if len(base) < 2:
            row.update(expected_cents=None, low_cents=None, high_cents=None,
                       note="Not enough history for this weekday yet (needs at least 2 past weeks).")
            out.append(row)
            continue
        expected = sum(base) / len(base)
        low, high = min(base), max(base)
        factor = 1.0
        if abs(trend - 1) >= 0.01:
            factor *= trend
            row["factors"].append({"name": "recent trend", "effect_pct": round((trend - 1) * 100, 1),
                                   "source": "last 14 days vs the 8 weeks before"})
        if day in special_all:
            name, kind = special_all[day]
            learned = learned_effect(history, name, special_all)
            eff = learned if learned is not None else holidays.default_effect(name, kind)
            factor *= eff
            row["holiday"] = name
            row["factors"].append({"name": name, "effect_pct": round((eff - 1) * 100, 1),
                                   "source": "your past sales on this day" if learned is not None
                                   else "starting assumption until your own history has this day"})
        w = weather_effect(weather.get(day))
        if w:
            factor *= w[0]
            row["factors"].append({"name": w[1], "effect_pct": round((w[0] - 1) * 100, 1),
                                   "source": "weather forecast (assumed effect on dine-in)"})
        row.update(expected_cents=round(expected * factor), low_cents=round(low * factor),
                   high_cents=round(high * factor), baseline_cents=round(expected), based_on_weeks=len(base))
        out.append(row)
    return out
