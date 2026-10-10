"""Tests for the dependency-free core: parsing, money/periods, roll-ups, pricing, routing, signatures.

Written with unittest so they run anywhere (`python -m unittest tests.test_core_logic`); pytest collects them too.
"""

import time
import unittest
from datetime import date, datetime, timezone

from app.agent.pricing import Usage, cost_usd
from app.agent.router import choose_model, needs_deep_model
from app.db_url import to_async_url
from app.integrations.pos import clover_parse
from app.integrations.reviews.parse import normalize_google_review
from app.integrations.twilio_sig import compute_signature, is_valid
from app.money import fmt_money, pct_change, previous_period, resolve_period
from app.oauth_state import InvalidState, make_state, read_state
from app.services.rollup_math import OrderRow, compute_rollups

# Shapes follow docs.clover.com examples (amounts in cents, times in epoch ms, collections in "elements").
CLOVER_ORDER = {
    "id": "ORD1",
    "total": 5350,
    "state": "locked",
    "paymentState": "PAID",
    "title": "Table 7",
    "createdTime": 1760000000000,
    "clientCreatedTime": 1760000000000,
    "modifiedTime": 1760003600000,
    "employee": {"id": "E1", "name": "Mia"},
    "lineItems": {"elements": [
        {"id": "L1", "name": "Margherita", "price": 2200, "item": {"id": "IT1"}},
        {"id": "L2", "name": "Margherita", "price": 2200, "item": {"id": "IT1"},
         "modifications": {"elements": [{"name": "Extra cheese", "amount": 300}]}},
        {"id": "L3", "name": "Prosciutto (per 100g)", "price": 1000, "unitQty": 650, "item": {"id": "IT2"}},
        {"id": "L4", "name": "Refunded drink", "price": 600, "refunded": True},
    ]},
    "payments": {"elements": [
        {"id": "P1", "amount": 5350, "taxAmount": 486, "tipAmount": 500, "result": "SUCCESS"},
        {"id": "P2", "amount": 5350, "taxAmount": 486, "tipAmount": 500, "result": "FAIL"},
    ]},
}


class CloverParsingTest(unittest.TestCase):
    def test_order_normalization(self):
        o = clover_parse.normalize_order(CLOVER_ORDER)
        self.assertEqual(o.external_id, "ORD1")
        self.assertEqual(o.status, "paid")
        self.assertEqual(o.total_cents, 5350)
        self.assertEqual(o.tax_cents, 486)  # failed payment ignored
        self.assertEqual(o.tip_cents, 500)
        self.assertEqual(o.table_name, "Table 7")
        self.assertEqual(o.employee_name, "Mia")
        self.assertEqual(o.opened_at, datetime.fromtimestamp(1760000000, tz=timezone.utc))
        self.assertIsNotNone(o.closed_at)
        self.assertEqual(len(o.lines), 3)  # refunded line dropped
        self.assertEqual(o.lines[1].total_cents, 2500)  # modifier added
        self.assertAlmostEqual(o.lines[2].qty, 0.65)  # unitQty / 1000
        self.assertEqual(o.lines[2].total_cents, 650)
        self.assertEqual(o.lines[0].item_external_id, "IT1")

    def test_open_and_void_orders(self):
        self.assertEqual(clover_parse.normalize_order({"id": "X", "state": "open"}).status, "open")
        self.assertEqual(clover_parse.normalize_order({"id": "X", "deleted": True}).status, "void")
        self.assertEqual(clover_parse.normalize_order({"id": "X", "paymentState": "REFUNDED"}).status, "refunded")

    def test_table_heuristic_ignores_non_tables(self):
        for title, expected in (("Table 12", "Table 12"), ("T4", "T4"), ("Takeaway", None), ("Tom's order", None)):
            o = clover_parse.normalize_order({"id": "X", "title": title})
            self.assertEqual(o.table_name, expected, title)

    def test_item_normalization(self):
        item = clover_parse.normalize_item({
            "id": "IT1", "name": "Peroni", "price": 1100, "cost": 350, "unitName": "bottle",
            "itemStock": {"quantity": 18}, "categories": {"elements": [{"name": "Drinks"}]},
        })
        self.assertEqual((item.name, item.category, item.price_cents, item.cost_cents), ("Peroni", "Drinks", 1100, 350))
        self.assertEqual(item.stock_qty, 18.0)
        self.assertTrue(item.active)
        hidden = clover_parse.normalize_item({"id": "IT2", "name": "Old", "hidden": True})
        self.assertFalse(hidden.active)
        self.assertIsNone(hidden.stock_qty)

    def test_webhook_parsing(self):
        body = {"appId": "APP", "merchants": {
            "M1": [{"objectId": "O:GHI", "type": "CREATE", "ts": 1537970958000},
                   {"objectId": "I:ITM", "type": "UPDATE", "ts": 1537970958001}],
            "M2": [{"objectId": "IC:CAT", "type": "DELETE", "ts": 1}, {"objectId": "bad"}],
        }}
        changes = clover_parse.parse_webhook(body)
        self.assertEqual([(c.merchant_id, c.object_type, c.object_id, c.change) for c in changes], [
            ("M1", "order", "GHI", "CREATE"), ("M1", "item", "ITM", "UPDATE"), ("M2", "category", "CAT", "DELETE"),
        ])

    def test_auth_header(self):
        self.assertTrue(clover_parse.verify_auth_header("abc-123", "abc-123"))
        self.assertFalse(clover_parse.verify_auth_header("abc-124", "abc-123"))
        self.assertFalse(clover_parse.verify_auth_header(None, "abc-123"))
        self.assertFalse(clover_parse.verify_auth_header("x", ""))  # unconfigured server rejects everything


class GoogleParsingTest(unittest.TestCase):
    def test_review(self):
        r = normalize_google_review({
            "name": "accounts/1/locations/2/reviews/R9",
            "reviewer": {"displayName": "Sam"},
            "starRating": "TWO",
            "comment": "(Translated by Google) Slow\n\n(Original)\nLento",
            "createTime": "2026-10-01T03:04:05.123456789Z",
            "reviewReply": {"comment": "Sorry!", "updateTime": "2026-10-02T00:00:00Z"},
        })
        self.assertEqual(r.external_id, "accounts/1/locations/2/reviews/R9")
        self.assertEqual((r.author, r.rating, r.text, r.reply_text), ("Sam", 2, "Lento", "Sorry!"))
        self.assertEqual(r.posted_at, datetime(2026, 10, 1, 3, 4, 5, 123456, tzinfo=timezone.utc))


class MoneyAndPeriodsTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(fmt_money(123456, "AUD"), "$1,234.56")
        self.assertEqual(fmt_money(-500, "GBP"), "-£5.00")
        self.assertEqual(fmt_money(None), "-")

    def test_periods(self):
        today = date(2026, 10, 10)  # a Saturday
        self.assertEqual(resolve_period("today", today), (today, today))
        self.assertEqual(resolve_period("yesterday", today), (date(2026, 10, 9),) * 2)
        self.assertEqual(resolve_period("this_week", today), (date(2026, 10, 5), today))
        self.assertEqual(resolve_period("last_week", today), (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(resolve_period("last_month", today), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(resolve_period("2026-10-03..2026-10-01", today), (date(2026, 10, 1), date(2026, 10, 3)))
        with self.assertRaises(ValueError):
            resolve_period("fortnight", today)

    def test_comparisons(self):
        s, e = date(2026, 10, 5), date(2026, 10, 11)
        self.assertEqual(previous_period(s, e), (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(previous_period(s, s, "same_weekday_last_week"), (date(2026, 9, 28),) * 2)
        self.assertEqual(previous_period(s, s, "last_year")[0].weekday(), s.weekday())
        self.assertEqual(pct_change(110, 100), 10.0)
        self.assertIsNone(pct_change(5, 0))


class RollupTest(unittest.TestCase):
    def test_local_days_and_exclusions(self):
        utc = timezone.utc
        orders = [
            # 13:30 UTC on 9 Oct is 00:30 on 10 Oct in Sydney (AEDT, UTC+11)
            OrderRow("paid", datetime(2026, 10, 9, 13, 30, tzinfo=utc), 1000, 91, 100, 2, [("Pizza", "Mains", 1, 1000)]),
            OrderRow("open", datetime(2026, 10, 10, 1, 0, tzinfo=utc), 500, 45, 0, None, [("Pizza", "Mains", 1, 500)]),
            OrderRow("void", datetime(2026, 10, 10, 2, 0, tzinfo=utc), 9999, 0, 0, None, [("Pizza", "Mains", 1, 9999)]),
            OrderRow("paid", datetime(2026, 10, 9, 2, 0, tzinfo=utc), 700, 0, 0, None, [("Wine", None, 2, 700)]),
        ]
        days, items = compute_rollups(orders, "Australia/Sydney")
        d10 = days[date(2026, 10, 10)]
        self.assertEqual((d10.gross_cents, d10.orders_count, d10.tip_cents, d10.guests), (1500, 2, 100, 2))
        self.assertEqual(d10.by_hour, {"0": 1000, "12": 500})
        self.assertEqual(days[date(2026, 10, 9)].gross_cents, 700)
        self.assertEqual(items[(date(2026, 10, 10), "Pizza")], ("Mains", 2.0, 1500))
        self.assertEqual(items[(date(2026, 10, 9), "Wine")], (None, 2.0, 700))


class PricingAndRoutingTest(unittest.TestCase):
    def test_haiku_cost(self):
        u = Usage(input_tokens=3000, output_tokens=500, cache_read_tokens=5000)
        # 3000*0.10 + 500*0.50 + 5000*0.01 per million
        self.assertAlmostEqual(cost_usd("claude-haiku-5-5", u), 0.0006, places=7)

    def test_haiku_long_prompt_rate(self):
        u = Usage(input_tokens=120_000, output_tokens=0)
        self.assertAlmostEqual(cost_usd("claude-haiku-5-5", u), 0.06, places=6)

    def test_sonnet_cost(self):
        u = Usage(input_tokens=3000, output_tokens=500, cache_read_tokens=5000)
        self.assertAlmostEqual(cost_usd("claude-sonnet-5-5", u), 0.0115, places=6)

    def test_routing(self):
        kw = {"default_model": "cheap", "deep_model": "deep"}
        self.assertFalse(needs_deep_model("How were sales yesterday?"))
        self.assertTrue(needs_deep_model("Why were sales down on Tuesday?"))
        self.assertEqual(choose_model("Top items this week", deep_requested=False, budget_exceeded=False, **kw), "cheap")
        self.assertEqual(choose_model("Forecast next week", deep_requested=False, budget_exceeded=False, **kw), "deep")
        self.assertEqual(choose_model("Top items", deep_requested=True, budget_exceeded=False, **kw), "deep")
        self.assertEqual(choose_model("Why?", deep_requested=True, budget_exceeded=True, **kw), "cheap")


class DatabaseUrlTest(unittest.TestCase):
    def test_railway_and_heroku_urls(self):
        self.assertEqual(to_async_url("postgresql://u:p@postgres.railway.internal:5432/railway"),
                         "postgresql+asyncpg://u:p@postgres.railway.internal:5432/railway")
        self.assertEqual(to_async_url("postgres://u:p@h/db"), "postgresql+asyncpg://u:p@h/db")

    def test_sslmode_becomes_ssl(self):
        self.assertEqual(to_async_url("postgresql://u:p@h/db?sslmode=require"), "postgresql+asyncpg://u:p@h/db?ssl=require")
        self.assertEqual(to_async_url("postgresql://u:p@h/db?sslmode=disable"), "postgresql+asyncpg://u:p@h/db")

    def test_leaves_other_urls_alone(self):
        for url in ("postgresql+asyncpg://u@h/db", "sqlite+aiosqlite:///./test.db", ""):
            self.assertEqual(to_async_url(url), url)


class SignaturesTest(unittest.TestCase):
    def test_oauth_state_roundtrip_and_tamper(self):
        st = make_state("secret", location_id="L1", user_id="U1", provider="clover")
        self.assertEqual(read_state("secret", st, provider="clover"), {"location_id": "L1", "user_id": "U1"})
        with self.assertRaises(InvalidState):
            read_state("other-secret", st, provider="clover")
        with self.assertRaises(InvalidState):
            read_state("secret", st, provider="google")
        body, sig = st.split(".")
        with self.assertRaises(InvalidState):
            read_state("secret", body[:-2] + "AA." + sig, provider="clover")
        expired = make_state("secret", location_id="L1", user_id="U1", provider="clover", ttl_s=-1)
        time.sleep(0.01)
        with self.assertRaises(InvalidState):
            read_state("secret", expired, provider="clover")

    def test_twilio_signature(self):
        # Example from Twilio's security docs.
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
                  "From": "+12349013030", "To": "+18005551212"}
        sig = compute_signature("12345", url, params)
        self.assertEqual(sig, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
        self.assertTrue(is_valid("12345", url, params, sig))
        self.assertFalse(is_valid("12345", url, params | {"Digits": "9"}, sig))


if __name__ == "__main__":
    unittest.main()
