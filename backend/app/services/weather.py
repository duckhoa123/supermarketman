"""Daily weather forecasts from Open-Meteo, stored per restaurant for the sales forecast.

Open-Meteo's free API is for non-commercial use only, so in production weather is fetched only when
OPEN_METEO_API_KEY (a paid plan) is set; without it the forecast simply leaves weather out.
"""

import logging
from datetime import date

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Location, WeatherDay, utcnow
from app.services.notify import http

log = logging.getLogger(__name__)
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code"


def weather_url() -> str | None:
    s = get_settings()
    if s.open_meteo_api_key:
        return "https://customer-api.open-meteo.com/v1/forecast"
    if s.is_production:
        return None
    return "https://api.open-meteo.com/v1/forecast"


def parse_daily(body: dict) -> list[dict]:
    d = body.get("daily") or {}
    out = []
    for i, day in enumerate(d.get("time") or []):
        def val(key, i=i):
            values = d.get(key) or []
            return values[i] if i < len(values) else None
        out.append({"day": date.fromisoformat(day), "temp_max_c": val("temperature_2m_max"),
                    "temp_min_c": val("temperature_2m_min"), "precip_mm": val("precipitation_sum"),
                    "weather_code": val("weather_code")})
    return out


async def refresh_weather(session: AsyncSession, location: Location) -> int:
    url = weather_url()
    if url is None or location.lat is None or location.lng is None:
        return 0
    params = {"latitude": location.lat, "longitude": location.lng, "daily": DAILY,
              "timezone": location.timezone, "forecast_days": 10}
    key = get_settings().open_meteo_api_key
    if key:
        params["apikey"] = key
    resp = await http().get(url, params=params)
    resp.raise_for_status()
    rows = parse_daily(resp.json())
    if rows:
        await session.execute(delete(WeatherDay).where(
            WeatherDay.location_id == location.id, WeatherDay.day.in_([r["day"] for r in rows])))
        for r in rows:
            session.add(WeatherDay(location_id=location.id, fetched_at=utcnow(), **r))
    await session.commit()
    return len(rows)
