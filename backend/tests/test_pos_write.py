"""Items and orders made in Pass go to Clover; Clover's replies are simulated."""

import json

import httpx
import respx
from sqlalchemy import select

from app.config import get_settings
from app.integrations.pos.base import OutLine, POSWriteError
from app.integrations.pos.clover import CloverAdapter
from app.models import CustomerOrder, Item, Location, Order, utcnow
from app.services.integration_store import save_tokens
from app.integrations.pos.base import TokenSet
from tests.conftest import OWNER

MID = "PF2YPSMFAZYV1"


def _api() -> str:
    return f"{get_settings().clover_api_base}/v3/merchants/{MID}"


async def test_adapter_creates_items_with_categories_and_stock():
    with respx.mock() as m:
        made = m.post(f"{_api()}/items").mock(return_value=httpx.Response(200, json={"id": "ITEM1"}))
        m.get(f"{_api()}/categories").mock(return_value=httpx.Response(200, json={"elements": [
            {"id": "CAT1", "name": "Meat"}]}))
        link = m.post(f"{_api()}/category_items").mock(return_value=httpx.Response(200, json={}))
        stock = m.post(f"{_api()}/item_stocks/ITEM1").mock(return_value=httpx.Response(200, json={}))
        item = await CloverAdapter().create_item("tok", MID, name="Chicken", price_cents=1200, category="meat",
                                                 stock_qty=20)
    assert item.external_id == "ITEM1"
    assert json.loads(made.calls[0].request.content) == {"name": "Chicken", "price": 1200, "priceType": "FIXED"}
    assert json.loads(link.calls[0].request.content) == {"elements": [{"category": {"id": "CAT1"}, "item": {"id": "ITEM1"}}]}
    assert json.loads(stock.calls[0].request.content) == {"quantity": 20}


async def test_adapter_moves_category_and_creates_missing_ones():
    with respx.mock() as m:
        m.post(f"{_api()}/items/ITEM1").mock(return_value=httpx.Response(200, json={"id": "ITEM1"}))
        m.get(f"{_api()}/categories").mock(return_value=httpx.Response(200, json={"elements": [
            {"id": "CAT1", "name": "Meat"}]}))
        newcat = m.post(f"{_api()}/categories").mock(return_value=httpx.Response(200, json={"id": "CAT2"}))
        links = m.post(f"{_api()}/category_items").mock(return_value=httpx.Response(200, json={}))
        await CloverAdapter().update_item("tok", MID, "ITEM1", name="Roast chicken", price_cents=1400,
                                          category="Mains", old_category="Meat", stock_qty=None, stock_changed=False)
    assert json.loads(newcat.calls[0].request.content) == {"name": "Mains"}
    removed, added = links.calls[0].request, links.calls[1].request
    assert removed.url.params["delete"] == "true" and b"CAT1" in removed.content and b"CAT2" in added.content


async def test_adapter_creates_an_open_order_one_line_per_unit():
    with respx.mock() as m:
        order = m.post(f"{_api()}/orders").mock(return_value=httpx.Response(200, json={"id": "ORD1"}))
        lines = m.post(f"{_api()}/orders/ORD1/line_items").mock(return_value=httpx.Response(200, json={"id": "L"}))
        total = m.post(f"{_api()}/orders/ORD1").mock(return_value=httpx.Response(200, json={"id": "ORD1"}))
        oid = await CloverAdapter().create_order("tok", MID, [
            OutLine("Chicken", 1200, 2, "ITEM1"), OutLine("Extra sauce", 150, 1)], title="Table 4", note="No onion")
    assert oid == "ORD1"
    assert json.loads(order.calls[0].request.content) == {"state": "open", "title": "Table 4", "note": "No onion"}
    sent = [json.loads(c.request.content) for c in lines.calls]
    assert sent == [{"item": {"id": "ITEM1"}}, {"item": {"id": "ITEM1"}}, {"name": "Extra sauce", "price": 150}]
    assert json.loads(total.calls[0].request.content) == {"total": 2550}


async def test_missing_write_permission_is_explained():
    with respx.mock() as m:
        m.post(f"{_api()}/items").mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
        try:
            await CloverAdapter().create_item("tok", MID, name="X", price_cents=100)
            raise AssertionError("expected an error")
        except POSWriteError as exc:
            assert "Inventory write" in str(exc)
        m.post(f"{_api()}/orders").mock(return_value=httpx.Response(400, json={"message": "Invalid title"}))
        try:
            await CloverAdapter().create_order("tok", MID, [OutLine("X", 100)], title="x")
            raise AssertionError("expected an error")
        except POSWriteError as exc:
            assert str(exc) == "Clover said: Invalid title"


class FakeClover:
    """Records what Pass asks Clover to do."""
    provider = "clover"

    def __init__(self):
        self.calls = []
        self.fail: str | None = None

    async def create_item(self, token, mid, **kw):
        from app.integrations.pos.base import NormalizedItem

        self.calls.append(("create_item", kw))
        if self.fail:
            raise POSWriteError(self.fail)
        return NormalizedItem(external_id="CLV-NEW", name=kw["name"], price_cents=kw["price_cents"])

    async def update_item(self, token, mid, item_id, **kw):
        self.calls.append(("update_item", item_id, kw))

    async def delete_item(self, token, mid, item_id):
        if self.fail:
            raise POSWriteError(self.fail)
        self.calls.append(("delete_item", item_id))

    async def create_order(self, token, mid, lines, *, title, note=None):
        if self.fail:
            raise POSWriteError(self.fail)
        self.calls.append(("create_order", lines, title, note))
        return f"CLV-ORD-{len(self.calls)}"


async def _connect(session, location_id):
    location = await session.get(Location, location_id)
    await save_tokens(session, location, "clover", TokenSet("tok", None, None, MID))
    await session.commit()


async def test_items_and_orders_from_pass_go_to_clover(client, session, demo_location_id):
    from app.integrations.pos.registry import set_adapter

    base = f"/locations/{demo_location_id}"
    # No POS: items stay in Pass, orders can't be sent.
    assert (await client.get(f"{base}/pos-write", headers=OWNER)).json() == {"pos": None}
    local = (await client.post(f"{base}/items", headers=OWNER, json={"name": "Soup", "price_cents": 900})).json()
    assert local["provider"] == "pass"
    r = await client.post(f"{base}/pos-orders", headers=OWNER, json={"lines": [{"item_id": local["id"], "qty": 1}]})
    assert r.status_code == 422 and "Connect Clover" in r.json()["detail"]

    fake = FakeClover()
    set_adapter("clover", fake)
    try:
        await _connect(session, demo_location_id)
        assert (await client.get(f"{base}/pos-write", headers=OWNER)).json() == {"pos": "clover"}
        new = (await client.post(f"{base}/items", headers=OWNER, json={
            "name": " Chicken ", "price_cents": 1200, "category": "Meat", "stock_qty": 20})).json()
        assert new["provider"] == "clover" and fake.calls[0] == ("create_item", {
            "name": "Chicken", "price_cents": 1200, "category": "Meat", "stock_qty": 20})
        item = await session.get(Item, new["id"])
        assert item.external_id == "CLV-NEW"

        ok = await client.put(f"{base}/items/{new['id']}/details", headers=OWNER, json={"price_cents": 1350})
        assert ok.status_code == 200
        kind, item_id, kw = fake.calls[1]
        assert (kind, item_id, kw["price_cents"], kw["name"], kw["stock_changed"]) == ("update_item", "CLV-NEW", 1350,
                                                                                      "Chicken", False)
        # Demo items aren't in Clover: edited in Pass only.
        demo = await session.scalar(select(Item).where(Item.location_id == demo_location_id, Item.provider == "demo"))
        r = await client.put(f"{base}/items/{demo.id}/details", headers=OWNER, json={"price_cents": 1})
        assert r.status_code == 200
        assert len(fake.calls) == 2

        sent = (await client.post(f"{base}/pos-orders", headers=OWNER, json={
            "lines": [{"item_id": new["id"], "qty": 2}, {"name": "Delivery fee", "unit_price_cents": 300}],
            "note": "Table 4"})).json()
        assert sent["total_cents"] == 2 * 1350 + 300
        _, lines, title, note = fake.calls[2]
        assert [(ln.name, ln.qty, ln.item_external_id) for ln in lines] == [("Chicken", 2, "CLV-NEW"),
                                                                           ("Delivery fee", 1, None)]
        assert title == "Order from Pass" and note == "Table 4"
        orders = (await client.get(f"{base}/orders", headers=OWNER)).json()
        assert any(o["total"] == "$30.00" for o in orders["orders"])

        fake.fail = "Clover didn't allow Pass to make this change. … Orders write permission …"
        r = await client.post(f"{base}/pos-orders", headers=OWNER, json={"lines": [{"item_id": new["id"]}]})
        assert r.status_code == 422 and "Orders write" in r.json()["detail"]
    finally:
        set_adapter("clover", None)


async def test_accepted_app_orders_are_sent_to_clover(session, demo_location_id):
    from app.integrations.pos.registry import set_adapter
    from app.services.handlers import handle_push_order_to_pos

    fake = FakeClover()
    set_adapter("clover", fake)
    try:
        await _connect(session, demo_location_id)
        item = await session.scalar(select(Item).where(Item.location_id == demo_location_id))
        order = CustomerOrder(location_id=demo_location_id, customer_user_id="u1", fulfilment="pickup", status="accepted",
                              lines=[{"item_id": item.id, "name": item.name, "qty": 2,
                                      "unit_price_cents": item.price_cents}],
                              subtotal_cents=2 * item.price_cents, total_cents=2 * item.price_cents, payment_status="paid",
                              notes="Extra napkins", placed_at=utcnow())
        session.add(order)
        await session.commit()
        await handle_push_order_to_pos(session, {"customer_order_id": order.id})
        await handle_push_order_to_pos(session, {"customer_order_id": order.id})  # retried: sent once only
        await session.refresh(order)
        assert order.pos_order_id and order.pos_error is None
        orders = [c for c in fake.calls if c[0] == "create_order"]
        assert len(orders) == 1
        _, lines, title, note = orders[0]
        assert title.startswith("Pass app pickup #") and "PAID in the Pass app" in note and "Extra napkins" in note
        assert lines[0].qty == 2 and lines[0].item_external_id is None  # a demo item: sent by name and price
        assert await session.scalar(select(Order.id).where(Order.external_id == order.pos_order_id))

        fake.fail = "Clover said: nope"
        second = CustomerOrder(location_id=demo_location_id, customer_user_id="u1", fulfilment="delivery",
                               status="accepted", lines=[{"name": "Soup", "qty": 1, "unit_price_cents": 900}],
                               payment_status="paid", placed_at=utcnow())
        session.add(second)
        await session.commit()
        await handle_push_order_to_pos(session, {"customer_order_id": second.id})
        await session.refresh(second)
        assert second.pos_order_id is None and second.pos_error == "Clover said: nope"
    finally:
        set_adapter("clover", None)


async def test_deleting_items(client, session, demo_location_id):
    from app.integrations.pos.registry import set_adapter
    from app.models import PurchaseOrder

    base = f"/locations/{demo_location_id}"
    stock = (await client.get(f"{base}/inventory", headers=OWNER)).json()["items"]
    burrata = next(i for i in stock if i["name"] == "Burrata & Tomato")
    tiramisu = next(i for i in stock if i["name"] == "Tiramisu")
    await client.post(f"{base}/purchase-orders/draft", headers=OWNER)  # the demo's low items go into a draft
    r = (await client.post(f"{base}/items/delete", headers=OWNER, json={"ids": [burrata["id"], "not-an-item"]})).json()
    assert r == {"deleted": 1, "deleted_in_pos": 0, "failed": [], "pos": None}
    names = [i["name"] for i in (await client.get(f"{base}/inventory", headers=OWNER)).json()["items"]]
    assert "Burrata & Tomato" not in names and "Tiramisu" in names
    drafts = list(await session.scalars(select(PurchaseOrder).where(PurchaseOrder.location_id == demo_location_id,
                                                                     PurchaseOrder.status == "draft")))
    assert all(ln["item_id"] != burrata["id"] for po in drafts for ln in po.lines)  # dropped from the draft order
    assert (await client.post(f"{base}/items/delete", headers={"Authorization": "Bearer dev:intruder@example.com"},
                              json={"ids": [tiramisu["id"]]})).status_code == 404

    fake = FakeClover()
    set_adapter("clover", fake)
    try:
        await _connect(session, demo_location_id)
        new = (await client.post(f"{base}/items", headers=OWNER, json={"name": "Chicken", "price_cents": 1200})).json()
        fake.fail = "Clover didn't allow Pass to make this change."
        r = (await client.post(f"{base}/items/delete", headers=OWNER, json={"ids": [new["id"], tiramisu["id"]]})).json()
        assert r["deleted"] == 1
        assert r["failed"] == [{"name": "Chicken", "error": "Clover didn't allow Pass to make this change."}]
        fake.fail = None
        r = (await client.post(f"{base}/items/delete", headers=OWNER, json={"ids": [new["id"]]})).json()
        assert r == {"deleted": 1, "deleted_in_pos": 1, "failed": [], "pos": "clover"}
        assert ("delete_item", "CLV-NEW") in fake.calls
    finally:
        set_adapter("clover", None)
