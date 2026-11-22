"""Restock alerts: when an item runs out and how much to order, from how fast it's used."""

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Alert, Item, Location, StockMovement, Supplier, utcnow
from app.services import restock
from tests.conftest import OWNER

TZ = ZoneInfo("America/Vancouver")
MON = datetime(2026, 11, 23, 9, 0, tzinfo=TZ)  # a Monday morning
TUE, FRI = 1, 4


class RestockMathTests(unittest.TestCase):
    def test_next_deliveries_respect_the_order_cutoff(self):
        # Tuesday and Friday, order by 2 pm the day before.
        nxt = restock.deliveries(MON, [TUE, FRI], "14:00", 1)
        assert [d.on for d in nxt] == [date(2026, 11, 24), date(2026, 11, 27)]
        assert nxt[0].order_by == datetime(2026, 11, 23, 14, 0, tzinfo=TZ)
        # After 2 pm Monday, Tuesday's delivery can't be ordered any more.
        late = restock.deliveries(MON.replace(hour=15), [TUE, FRI], "14:00", 1)
        assert late[0].on == date(2026, 11, 27)
        assert restock.deliveries(MON, [], "14:00", 1) == []
        # ...but it's still coming if it was ordered: running out is judged against it.
        assert restock.next_arrival(MON.date(), [TUE, FRI]) == date(2026, 11, 24)
        assert restock.next_arrival(date(2026, 11, 24), [TUE, FRI], received_today=True) == date(2026, 11, 27)

    def test_salmon_monday_order_reminder(self):
        """49 lb on Monday, 17 lb a day, ready the day after delivery, 2 days of margin: order 70 lb for Tuesday."""
        nxt = restock.deliveries(MON, [TUE, FRI], "14:00", 1)
        p = restock.plan(49, 17, MON, nxt, ready_days=1, safety_days=2, arriving=date(2026, 11, 24))
        assert p.status == "tight"  # lasts until Wednesday (34 lb), but not with 2 days to spare
        assert p.order_today and p.next_delivery == date(2026, 11, 24) and p.ready_on == date(2026, 11, 25)
        assert p.order_qty == 70  # Wed–Sat 51 lb + 34 lb margin − 15 lb left on Wednesday
        assert p.lasts_days == 2.9
        assert p.reorder_point == 102  # (3 days between deliveries + 1 to be ready) × 17 + 34

    def test_salmon_busy_saturday_runs_out(self):
        sat = datetime(2026, 11, 28, 20, 0, tzinfo=TZ)
        nxt = restock.deliveries(sat, [TUE, FRI], "14:00", 1)
        p = restock.plan(30, 17, sat, nxt, ready_days=1, safety_days=2, arriving=date(2026, 12, 1))
        assert p.status == "runs_out" and p.runs_out_on == date(2026, 11, 29)  # Sunday
        assert p.ready_on == date(2026, 12, 2) and not p.order_today

    def test_plenty_left_needs_no_order(self):
        nxt = restock.deliveries(MON, [TUE, FRI], "14:00", 1)
        p = restock.plan(400, 17, MON, nxt, ready_days=0, safety_days=2, arriving=date(2026, 11, 24))
        assert p.status == "ok" and p.order_qty == 0

    def test_without_delivery_days(self):
        # 1 day to get it, then the margin: 17 × (1 + 2) = 51 lb.
        assert restock.plan(45, 17, MON, []).status == "tight"
        assert restock.plan(60, 17, MON, []).status == "ok"
        assert restock.plan(10, 17, MON, []).status == "runs_out"


async def test_alerts_from_sales_pace(client, session, demo_location_id):
    loc = await session.get(Location, demo_location_id)
    loc.timezone = "America/Vancouver"
    sup = Supplier(location_id=demo_location_id, name="Ocean Fish Co")
    session.add(sup)
    await session.commit()
    today = restock.local_now(loc).date()
    # Delivers tomorrow and in 4 days; the order for tomorrow is in by 11:59 pm today.
    r = await client.patch(f"/locations/{demo_location_id}/suppliers/{sup.id}", headers=OWNER, json={
        "name": "Ocean Fish Co", "delivery_days": [(today.weekday() + 1) % 7, (today.weekday() + 4) % 7],
        "order_cutoff": "23:59", "order_lead_days": 1})
    assert r.status_code == 200 and r.json()["schedule"].startswith("Delivers ")
    salmon = Item(location_id=demo_location_id, provider="pass", external_id="pass-salmon", name="Salmon", unit="lb",
                  stock_qty=49, price_cents=0, active=True, supplier_id=sup.id, ready_days=1)
    session.add(salmon)
    await session.flush()
    # Four weeks of sales at 17 lb a day.
    start = utcnow() - timedelta(days=28)
    session.add(StockMovement(location_id=demo_location_id, item_id=salmon.id, item_name="Salmon", kind="opening",
                              qty_delta=0, qty_after=0, created_at=start))
    for d in range(28):
        session.add(StockMovement(location_id=demo_location_id, item_id=salmon.id, item_name="Salmon", kind="sale",
                                  qty_delta=-17,
                                  qty_after=49, created_at=start + timedelta(days=d, hours=1)))
    await session.commit()

    sent = await restock.check_restock(session, loc)
    alerts = list(await session.scalars(select(Alert).where(Alert.location_id == demo_location_id,
                                                            Alert.kind == "restock_order")))
    assert sent >= 1 and len(alerts) == 1
    assert alerts[0].title.startswith("Order from Ocean Fish Co by 11:59 pm for tomorrow")
    assert "Salmon 70 lb (49 lb left)" in alerts[0].body
    assert await restock.check_restock(session, loc) == 0  # not sent twice

    # A bad night: 20 lb left can't last until the delivery is ready the day after tomorrow.
    salmon = await session.get(Item, salmon.id)
    salmon.stock_qty = 20
    await session.commit()
    await restock.check_restock(session, loc)
    runout = await session.scalar(select(Alert).where(Alert.location_id == demo_location_id, Alert.kind == "runout"))
    assert runout is not None and runout.title == "Salmon is running out"

    # The inventory list shows the pace and a suggested reorder point.
    rows = (await client.get(f"/locations/{demo_location_id}/inventory/on-hand", headers=OWNER)).json()
    row = next(r for r in rows["items"] if r["name"] == "Salmon")
    assert row["restock"]["usage"] == 17 and row["ready_days"] == 1 and rows["safety_days"] == 2
