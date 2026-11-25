"""The Home charts: sales, orders, average ticket and tips over a range, with this month against last month."""

from datetime import date

from app.services.analytics import _bucket, _months_back
from tests.conftest import INTRUDER, OWNER


async def test_series_ranges(client, demo_location_id):
    base = f"/locations/{demo_location_id}/dashboard/series"
    daily = (await client.get(base, headers=OWNER)).json()
    assert daily["unit"] == "day" and len(daily["points"]) == 60
    assert sum(p["gross_cents"] for p in daily["points"]) > 0 and daily["last_month_label"]
    pt = next(p for p in daily["points"] if p["orders"])
    assert pt["avg_ticket_cents"] == round(pt["gross_cents"] / pt["orders"])
    weekly = (await client.get(f"{base}?range=3m", headers=OWNER)).json()
    assert weekly["unit"] == "week" and 13 <= len(weekly["points"]) <= 14
    # The same sales, bucketed differently, over the days both cover.
    monthly = (await client.get(f"{base}?range=12m", headers=OWNER)).json()
    assert len(monthly["points"]) == 12
    assert sum(p["gross_cents"] for p in monthly["points"]) >= sum(p["gross_cents"] for p in daily["points"])
    assert (await client.get(f"{base}?range=5y", headers=OWNER)).status_code == 422
    assert (await client.get(base, headers=INTRUDER)).status_code == 404


def test_buckets():
    assert _bucket(date(2026, 11, 26), "week") == date(2026, 11, 23)  # Monday
    assert _bucket(date(2026, 11, 26), "month") == date(2026, 11, 1)
    assert _months_back(date(2026, 1, 15), 2) == date(2025, 11, 1)
