"""Sales forecast and prep list for a restaurant, from its daily roll-ups, BC holidays and the weather."""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySales, ItemSalesDaily, Location, WeatherDay
from app.money import fmt_money
from app.services import forecast_math

HISTORY_DAYS = 400  # a year+ so last year's holidays can teach this year's forecast


async def sales_history(session: AsyncSession, location: Location, today: date) -> dict[date, int]:
    rows = await session.execute(
        select(DailySales.day, DailySales.gross_cents).where(
            DailySales.location_id == location.id,
            DailySales.day >= today - timedelta(days=HISTORY_DAYS),
            DailySales.day < today,  # today is partial
        )
    )
    return {d: int(g) for d, g in rows}


async def weather_for(session: AsyncSession, location: Location, start: date,
                      end: date) -> dict[date, forecast_math.Weather]:
    rows = await session.scalars(
        select(WeatherDay).where(WeatherDay.location_id == location.id, WeatherDay.day >= start, WeatherDay.day <= end)
    )
    return {r.day: forecast_math.Weather(r.temp_max_c, r.precip_mm, r.weather_code) for r in rows}


async def prep_list(session: AsyncSession, location: Location, day: date, factor: float, limit: int = 10) -> list[dict]:
    """Expected portions of the best sellers on `day`: their average on the last 4 same weekdays x the
    day's forecast factor. Helps decide what to prep and what to order."""
    days = [day - timedelta(days=7 * k) for k in range(1, 5)]
    rows = await session.execute(
        select(ItemSalesDaily.name, func.sum(ItemSalesDaily.qty))
        .where(ItemSalesDaily.location_id == location.id, ItemSalesDaily.day.in_(days))
        .group_by(ItemSalesDaily.name)
        .order_by(func.sum(ItemSalesDaily.qty).desc())
        .limit(limit)
    )
    return [{"name": n, "expected_qty": round(float(q) / len(days) * factor, 1)} for n, q in rows]


async def forecast(session: AsyncSession, location: Location, days: int = 7) -> dict:
    from app.services.analytics import local_today

    today = local_today(location)
    days = max(1, min(14, days))
    history = await sales_history(session, location, today)
    weather = await weather_for(session, location, today, today + timedelta(days=days))
    rows = forecast_math.forecast(history, today, days, weather)
    for r in rows:
        for key in ("expected", "low", "high"):
            cents = r.get(f"{key}_cents")
            r[key] = fmt_money(cents, location.currency) if cents is not None else None
    tomorrow = rows[0]
    prep: list[dict] = []
    if tomorrow.get("expected_cents") and tomorrow.get("baseline_cents"):
        prep = await prep_list(session, location, today + timedelta(days=1),
                               tomorrow["expected_cents"] / tomorrow["baseline_cents"])
    total = sum(r["expected_cents"] or 0 for r in rows)
    return {
        "from": rows[0]["day"], "to": rows[-1]["day"], "currency": location.currency,
        "days": rows,
        "total_expected": fmt_money(total, location.currency),
        "total_expected_cents": total,
        "weather_included": bool(weather),
        "prep_list_tomorrow": prep,
        "method": "average of the last 6 matching weekdays, adjusted for recent trend, holidays and weather",
    }
