"""Dependency-free tests for distances, fees, dispatch choice, opening hours, BC holidays and the forecast."""

import unittest
from datetime import date, datetime, timedelta

from app.services import forecast_math, geo, holidays


class GeoTests(unittest.TestCase):
    def test_distance_and_fee(self):
        # Main & Broadway to Waterfront Station is about 2.5 km straight line.
        km = geo.haversine_km(49.2634, -123.1007, 49.2860, -123.1115)
        self.assertAlmostEqual(km, 2.6, delta=0.2)
        self.assertEqual(geo.delivery_fee_cents(2.9, 399, 3.0, 75), 399)
        self.assertEqual(geo.delivery_fee_cents(3.0, 399, 3.0, 75), 399)
        self.assertEqual(geo.delivery_fee_cents(3.1, 399, 3.0, 75), 474)  # each started km beyond the base
        self.assertEqual(geo.delivery_fee_cents(5.0, 399, 3.0, 75), 549)

    def test_restaurant_fee_never_above_bc_cap(self):
        self.assertEqual(geo.restaurant_fee_cents(10_000, 1100), 1100)
        self.assertEqual(geo.restaurant_fee_cents(10_000, 3500), 2000)  # 20% cap

    def test_opening_hours(self):
        hours = {"fri": [["11:30", "14:00"], ["17:00", "01:00"]], "sat": [["12:00", "22:00"]]}
        self.assertTrue(geo.is_open(hours, datetime(2026, 10, 16, 12, 0)))  # Friday lunch
        self.assertFalse(geo.is_open(hours, datetime(2026, 10, 16, 15, 0)))  # Friday afternoon gap
        self.assertTrue(geo.is_open(hours, datetime(2026, 10, 17, 0, 30)))  # Friday's late range, Saturday 00:30
        self.assertFalse(geo.is_open(hours, datetime(2026, 10, 17, 1, 30)))
        self.assertIsNone(geo.is_open({}, datetime(2026, 10, 17, 12, 0)))

    def test_dispatch_prefers_batching_then_nearest_free(self):
        near_full = geo.DriverState("a", 49.2635, -123.1008, active=2)
        far = geo.DriverState("b", 49.2900, -123.1300)
        near = geo.DriverState("c", 49.2650, -123.1000)
        picked, reason = geo.choose_driver("L1", 49.2634, -123.1007, [near_full, far, near], 2, 20)
        self.assertEqual(picked.id, "c")
        self.assertIn("nearest", reason)
        heading = geo.DriverState("d", 49.2800, -123.1200, active=1, heading_to=("L1",))
        picked, reason = geo.choose_driver("L1", 49.2634, -123.1007, [near, heading], 2, 20)
        self.assertEqual(picked.id, "d")
        self.assertIn("same restaurant", reason)
        self.assertIsNone(geo.choose_driver("L1", 49.2634, -123.1007, [near_full], 2, 20))
        self.assertIsNone(geo.choose_driver("L1", 49.2634, -123.1007, [far], 2, 1))  # too far to pick up

    def test_eta_waits_for_food(self):
        quick = geo.delivery_eta_minutes(0.0, 3.0, "e_bike", prep_left_min=0)
        waiting = geo.delivery_eta_minutes(0.0, 3.0, "e_bike", prep_left_min=15)
        self.assertEqual(waiting - quick, 15)
        self.assertGreater(geo.delivery_eta_minutes(2.0, 3.0, "e_bike"), quick)


class HolidayTests(unittest.TestCase):
    def test_bc_statutory_2026(self):
        h = holidays.bc_statutory(2026)
        self.assertEqual(h[date(2026, 2, 16)], "Family Day")
        self.assertEqual(h[date(2026, 4, 3)], "Good Friday")
        self.assertEqual(h[date(2026, 5, 18)], "Victoria Day")
        self.assertEqual(h[date(2026, 8, 3)], "B.C. Day")
        self.assertEqual(h[date(2026, 10, 12)], "Thanksgiving")
        self.assertNotIn(date(2026, 12, 26), h)  # Boxing Day is not a BC statutory holiday
        self.assertEqual(len(h), 11)

    def test_special_days(self):
        s = holidays.special_days(2027)
        self.assertEqual(s[date(2027, 2, 6)], "Lunar New Year")
        self.assertEqual(s[date(2027, 5, 9)], "Mother's Day")
        self.assertEqual(holidays.easter(2027), date(2027, 3, 28))


def _history(today: date, days: int, base: int = 100_000, weekend: int = 150_000) -> dict[date, int]:
    return {today - timedelta(days=i): (weekend if (today - timedelta(days=i)).weekday() >= 4 else base)
            for i in range(1, days + 1)}


class ForecastTests(unittest.TestCase):
    def test_same_weekday_baseline(self):
        today = date(2026, 6, 1)  # a Monday, no holidays in the following week
        rows = forecast_math.forecast(_history(today, 70), today, 7)
        by_day = {r["weekday"]: r for r in rows}
        self.assertEqual(by_day["Tue"]["expected_cents"], 100_000)
        self.assertEqual(by_day["Sat"]["expected_cents"], 150_000)
        self.assertEqual(by_day["Tue"]["factors"], [])

    def test_holiday_default_then_learned(self):
        today = date(2026, 2, 10)
        history = _history(today, 70)
        valentines = next(r for r in forecast_math.forecast(history, today, 7) if r["day"] == "2026-02-14")
        self.assertEqual(valentines["holiday"], "Valentine's Day")
        self.assertEqual(valentines["expected_cents"], round(150_000 * 1.25))  # starting assumption
        self.assertIn("assumption", valentines["factors"][0]["source"])
        # With last year's Valentine's Day in the history (sales doubled), the learned effect replaces it.
        history = _history(today, 400)
        history[date(2025, 2, 14)] = history[date(2025, 2, 14)] * 2
        valentines = next(r for r in forecast_math.forecast(history, today, 7) if r["day"] == "2026-02-14")
        self.assertEqual(valentines["factors"][-1]["source"], "your past sales on this day")
        self.assertAlmostEqual(valentines["expected_cents"] / 150_000, 2.0, delta=0.05)

    def test_weather_and_short_history(self):
        today = date(2026, 6, 1)
        rain = {date(2026, 6, 2): forecast_math.Weather(temp_max_c=14, precip_mm=12, code=63)}
        tue = forecast_math.forecast(_history(today, 70), today, 1, rain)[0]
        self.assertEqual(tue["expected_cents"], 90_000)
        self.assertIn("rain", tue["factors"][0]["name"])
        short = forecast_math.forecast(_history(today, 7), today, 1)[0]
        self.assertIsNone(short["expected_cents"])


if __name__ == "__main__":
    unittest.main()
