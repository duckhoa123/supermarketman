"""Removing the demo menu and sales once the restaurant's own POS data is coming in."""

from datetime import timedelta

from sqlalchemy import func, select

from app.models import DailySales, Item, ItemSalesDaily, Order, OrderLine, utcnow
from tests.conftest import INTRUDER, OWNER


async def test_owner_removes_demo_items_and_sales_but_keeps_clover(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/demo-data"
    before = (await client.get(base, headers=OWNER)).json()
    assert before["items"] > 10 and before["orders"] > 100

    # Real data from Clover: one item and one paid order yesterday.
    session.add(Item(location_id=demo_location_id, provider="clover", external_id="CHK1", name="Chicken",
                     category="Meat", price_cents=1200))
    when = utcnow() - timedelta(days=1)
    order = Order(location_id=demo_location_id, provider="clover", external_id="5TY9XK4G41YSC", status="paid",
                  total_cents=1200, opened_at=when, closed_at=when)
    order.lines = [OrderLine(name="Chicken", category="Meat", qty=1, unit_price_cents=1200, total_cents=1200)]
    session.add(order)
    await session.commit()

    assert (await client.post(f"{base}/clear", headers=INTRUDER)).status_code == 404
    out = (await client.post(f"{base}/clear", headers=OWNER)).json()
    assert out["items"] == before["items"] and out["orders"] == before["orders"]
    assert (await client.get(base, headers=OWNER)).json() == {"items": 0, "orders": 0}

    session.expire_all()
    names = list(await session.scalars(select(Item.name).where(Item.location_id == demo_location_id)))
    assert names == ["Chicken"]
    assert await session.scalar(select(func.count(Order.id)).where(Order.location_id == demo_location_id)) == 1
    assert await session.scalar(select(func.count(OrderLine.id))) == 1
    days = list(await session.scalars(select(DailySales).where(DailySales.location_id == demo_location_id)))
    assert len(days) == 1 and days[0].gross_cents == 1200
    sold = list(await session.scalars(select(ItemSalesDaily.name).where(ItemSalesDaily.location_id == demo_location_id)))
    assert sold == ["Chicken"]
    stock = (await client.get(f"/locations/{demo_location_id}/inventory", headers=OWNER)).json()
    assert [i["name"] for i in stock["items"]] == ["Chicken"]
