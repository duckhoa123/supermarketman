"""The three agents working together through the API: restaurant operations, a customer order delivered by an
employed driver, bookings, avatars, and the Customer agent and Driver Assistant chats."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AIUsage,
    Alert,
    Delivery,
    Guest,
    Item,
    Location,
    NutritionEntry,
    Organization,
    PurchaseOrder,
)
from app.services import alerts
from app.services.jobs import run_pending
from tests.conftest import INTRUDER, OWNER, parse_sse, text_result, tool_result

CUSTOMER = {"Authorization": "Bearer dev:diner@example.com"}
DRIVER = {"Authorization": "Bearer dev:rider@example.com"}
ADMIN = {"Authorization": "Bearer dev:ops@example.com"}
HOME = {"home_address": "E 10th Ave & Quebec St (test)", "home_lat": 49.2620, "home_lng": -123.1030}


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_admin_emails", "ops@example.com")


async def _make_public(session, location_id: str) -> Location:
    """Demo restaurants are only visible to their own team; turn this one into a real listing for the test."""
    loc = await session.get(Location, location_id)
    org = await session.get(Organization, loc.org_id)
    org.plan = "trial"
    await session.commit()
    return loc


async def _item_id(session, location_id: str, name: str) -> str:
    return await session.scalar(select(Item.id).where(Item.location_id == location_id, Item.name == name))


async def test_forecast_and_reports(client, demo_location_id):
    fc = (await client.get(f"/locations/{demo_location_id}/forecast?days=7", headers=OWNER)).json()
    assert len(fc["days"]) == 7
    assert all(d["expected_cents"] for d in fc["days"]) and fc["total_expected_cents"] > 0
    assert fc["prep_list_tomorrow"] and fc["prep_list_tomorrow"][0]["expected_qty"] > 0

    day = (await client.get(f"/locations/{demo_location_id}/reports/day", headers=OWNER)).json()
    assert day["sales"]["orders"] > 0
    assert 10 < day["labour"]["labour_pct_of_sales"] < 80
    assert day["food_cost"]["food_cost_pct"] > 0
    assert day["text"].startswith("Yesterday:") and "Labour:" in day["text"] and "Next 7 days" in day["text"]
    week = (await client.get(f"/locations/{demo_location_id}/reports/week", headers=OWNER)).json()
    assert week["best_day"] and week["worst_day"]
    assert (await client.get(f"/locations/{demo_location_id}/reports/year", headers=OWNER)).status_code == 422
    assert (await client.get(f"/locations/{demo_location_id}/forecast", headers=INTRUDER)).status_code == 404

    labour = (await client.get(f"/locations/{demo_location_id}/labour?period=last_7_days", headers=OWNER)).json()
    assert labour["hours"] > 0 and {r["role"] for r in labour["by_role"]} >= {"kitchen", "floor"}
    insights = (await client.get(f"/locations/{demo_location_id}/marketing", headers=OWNER)).json()
    assert insights["slow_days"] and insights["guests"]["marketing_consent"] == 6


async def test_low_stock_drafts_purchase_order_and_approval_sends_it(client, session, demo_location_id):
    location = await session.get(Location, demo_location_id)
    await alerts.check_low_stock(session, location)
    alert = await session.scalar(select(Alert).where(Alert.location_id == demo_location_id,
                                                     Alert.title == "Low stock: Tiramisu"))
    assert "draft order to Harbour Dairy" in alert.body

    pos = (await client.get(f"/locations/{demo_location_id}/purchase-orders", headers=OWNER)).json()
    draft = next(p for p in pos if p["supplier"].startswith("Harbour Dairy"))
    assert draft["status"] == "draft" and {ln["name"] for ln in draft["lines"]} >= {"Tiramisu", "Burrata & Tomato"}
    tiramisu = next(ln for ln in draft["lines"] if ln["name"] == "Tiramisu")
    assert tiramisu["qty"] == 5  # twice the alert level (4) minus the 3 in stock

    # Drafting again doesn't duplicate lines.
    await client.post(f"/locations/{demo_location_id}/purchase-orders/draft", headers=OWNER)
    again = next(p for p in (await client.get(f"/locations/{demo_location_id}/purchase-orders",
                                              headers=OWNER)).json() if p["id"] == draft["id"])
    assert len(again["lines"]) == len(draft["lines"])

    r = await client.post(f"/locations/{demo_location_id}/purchase-orders/{draft['id']}/approve", headers=OWNER,
                          json={"note": "Before Friday please"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    await run_pending(time_budget_s=20)
    session.expire_all()
    po = await session.get(PurchaseOrder, draft["id"])
    assert po.status == "approved" and "email" in po.send_error  # no email provider in tests: nothing sent
    assert (await client.post(f"/locations/{demo_location_id}/purchase-orders/{draft['id']}/approve",
                              headers=OWNER)).status_code == 409

    stock_before = await session.scalar(select(Item.stock_qty).where(Item.id == tiramisu["item_id"]))
    assert (await client.post(f"/locations/{demo_location_id}/purchase-orders/{draft['id']}/received",
                              headers=OWNER)).status_code == 200
    session.expire_all()
    assert await session.scalar(select(Item.stock_qty).where(Item.id == tiramisu["item_id"])) == stock_before + 5


async def test_agent_avatars(client):
    me = (await client.get("/me", headers=OWNER)).json()
    assert [a["agent"] for a in me["agents"]] == ["restaurant_manager", "customer"]  # drivers are invite-only
    assert me["is_driver"] is False
    r = await client.put("/agents/customer/profile", headers=OWNER,
                         json={"display_name": "Basil", "icon": "leaf", "color": "#123456", "tone": "playful"})
    assert r.status_code == 200 and r.json()["display_name"] == "Basil" and r.json()["avatar"]["icon"] == "leaf"
    assert (await client.put("/agents/customer/profile", headers=OWNER, json={"icon": "unicorn"})).status_code == 422
    assert (await client.put("/agents/driver/profile", headers=OWNER, json={"tone": "concise"})).status_code == 403
    agents = (await client.get("/agents", headers=OWNER)).json()
    assert {a["agent"] for a in agents["agents"]} == {"restaurant_manager", "customer"}
    assert next(a for a in agents["agents"] if a["agent"] == "customer")["tone"] == "playful"


async def test_demo_restaurant_hidden_from_other_customers(client, demo_location_id):
    found = (await client.get("/customer/restaurants?lat=49.262&lng=-123.103", headers=CUSTOMER)).json()
    assert found["count"] == 0
    assert (await client.get(f"/customer/restaurants/{demo_location_id}/menu", headers=CUSTOMER)).status_code == 404
    own = (await client.get("/customer/restaurants?lat=49.262&lng=-123.103", headers=OWNER)).json()
    assert [r["id"] for r in own["restaurants"]] == [demo_location_id]  # the owner can try their own listing


async def test_order_delivered_by_employed_driver(client, session, demo_location_id, admin):
    loc = await _make_public(session, demo_location_id)
    await client.put("/customer/profile", headers=CUSTOMER, json={**HOME, "calories_goal": 2200})

    found = (await client.get("/customer/restaurants?q=carbonara&fulfilment=delivery", headers=CUSTOMER)).json()
    assert found["restaurants"][0]["id"] == loc.id
    assert found["restaurants"][0]["matching_items"][0]["name"] == "Spaghetti Carbonara"
    veg = (await client.get("/customer/restaurants?diet=vegetarian&max_calories=500", headers=CUSTOMER)).json()
    assert {i["name"] for i in veg["restaurants"][0]["matching_items"]} <= {
        "Burrata & Tomato", "Garlic Bread", "Arancini (3)", "Tiramisu", "Panna Cotta"}

    carbonara = await _item_id(session, loc.id, "Spaghetti Carbonara")
    garlic = await _item_id(session, loc.id, "Garlic Bread")
    body = {"restaurant_id": loc.id, "items": [{"item_id": carbonara, "qty": 2}, {"item_id": garlic}],
            "fulfilment": "delivery", "share_contact": True, "marketing_consent": False}
    q = (await client.post("/customer/orders/quote", headers=CUSTOMER, json=body)).json()
    assert q["delivery_fee"] == "$3.99" and q["nutrition"]["calories"] == 2 * 780 + 310
    assert q["total_cents"] == 6100 + 305 + 399  # food + 5% GST + delivery fee

    placed = await client.post("/customer/orders", headers=CUSTOMER, json=body)
    assert placed.status_code == 200, placed.text
    order = placed.json()["order"]
    assert placed.json()["payment"]["status"] == "test"  # no Stripe key: nothing charged
    order_id = order["id"]

    # The restaurant sees it, with the platform fee (11%, under BC's 20% cap).
    board = (await client.get(f"/locations/{loc.id}/app-orders", headers=OWNER)).json()["orders"]
    assert board[0]["id"] == order_id and board[0]["platform_fee"] == "$6.71"
    assert await session.scalar(select(Alert.id).where(Alert.location_id == loc.id, Alert.kind == "new_order"))
    # Only legal moves: a restaurant can't mark a delivery order collected, a customer can't cancel once accepted.
    assert (await client.post(f"/locations/{loc.id}/app-orders/{order_id}/status", headers=OWNER,
                              json={"status": "picked_up"})).status_code == 409
    for status in ("accepted", "ready"):
        r = await client.post(f"/locations/{loc.id}/app-orders/{order_id}/status", headers=OWNER,
                              json={"status": status})
        assert r.status_code == 200, r.text
    assert (await client.post(f"/customer/orders/{order_id}/cancel", headers=CUSTOMER)).status_code == 409
    assert r.json()["delivery"]["status"] == "waiting"  # ready, but no driver on shift yet

    # Drivers are staff: invited by email, then they sign in.
    assert (await client.get("/driver/me", headers=DRIVER)).status_code == 403
    assert (await client.post("/fleet/drivers", headers=OWNER, json={"email": "x@example.com", "name": "X"})
            ).status_code == 403
    inv = await client.post("/fleet/drivers", headers=ADMIN, json={"email": "rider@example.com", "name": "Riley",
                                                                     "vehicle": "e_bike", "hourly_wage_cents": 2000})
    assert inv.status_code == 200, inv.text
    assert (await client.post("/fleet/drivers", headers=ADMIN, json={
        "email": "low@example.com", "name": "Low", "hourly_wage_cents": 1500})).status_code == 422  # below BC minimum
    me = (await client.get("/me", headers=DRIVER)).json()
    assert me["is_driver"] and [a["agent"] for a in me["agents"]][-1] == "driver"

    started = await client.post("/driver/shift/start", headers=DRIVER, json={"lat": 49.2640, "lng": -123.1010})
    jobs = started.json()["deliveries"]
    assert len(jobs) == 1 and jobs[0]["restaurant"] == loc.name and jobs[0]["status"] == "assigned"
    job_alert = await session.scalar(select(Alert).where(Alert.kind == "delivery_job"))
    assert job_alert.user_id == "dev-rider@example.com" and job_alert.location_id is None
    assert (await client.post("/driver/shift/end", headers=DRIVER)).status_code == 409  # still has a job

    delivery_id = jobs[0]["id"]
    assert (await client.post(f"/driver/deliveries/{delivery_id}/status", headers=DRIVER,
                              json={"status": "delivered"})).status_code == 409  # pick it up first
    for status in ("picked_up", "delivered"):
        r = await client.post(f"/driver/deliveries/{delivery_id}/status", headers=DRIVER, json={"status": status})
        assert r.status_code == 200, r.text
    mine = (await client.get(f"/customer/orders/{order_id}", headers=CUSTOMER)).json()
    assert mine["status"] == "delivered" and mine["tip_suggestions"][0]["pct"] == 10
    updates = (await client.get("/me/alerts", headers=CUSTOMER)).json()["alerts"]
    assert {a["title"] for a in updates} >= {"Order accepted", "On the way", "Delivered"}

    # The meal went into the customer's nutrition log, and they joined the guest list.
    day = (await client.get("/customer/nutrition", headers=CUSTOMER)).json()
    assert day["totals"]["calories"] == 2 * 780 + 310 and day["remaining"]["calories"] == 2200 - 1870
    guest = await session.scalar(select(Guest).where(Guest.location_id == loc.id,
                                                     Guest.user_id == "dev-diner@example.com"))
    assert guest.visits == 1 and guest.marketing_consent is False

    # Tip after delivery: all of it to the driver.
    r = await client.post(f"/customer/orders/{order_id}/tip", headers=CUSTOMER, json={"tip_cents": 500})
    assert r.status_code == 200 and r.json()["tip"] == "$5.00"
    session.expire_all()
    delivery = await session.get(Delivery, delivery_id)
    assert delivery.tip_cents == 500
    stats = (await client.get("/driver/stats?period=today", headers=DRIVER)).json()
    assert stats["deliveries"] == 1 and stats["tips"] == "$5.00"
    assert (await client.post("/driver/shift/end", headers=DRIVER, json={"on_call": True})).json()["status"] == "on_call"

    # Someone else can't see or tip this order.
    assert (await client.get(f"/customer/orders/{order_id}", headers=INTRUDER)).status_code == 404
    overview = (await client.get("/fleet/overview", headers=ADMIN)).json()
    assert overview["today"]["deliveries"] == 1 and overview["on_call"] == 1


async def test_delivery_limited_to_service_area(client, session, demo_location_id):
    loc = await _make_public(session, demo_location_id)
    await client.put("/customer/profile", headers=CUSTOMER, json={"home_lat": 49.89, "home_lng": -119.49})  # Kelowna
    item = await _item_id(session, loc.id, "Margherita Pizza")
    r = await client.post("/customer/orders/quote", headers=CUSTOMER, json={
        "restaurant_id": loc.id, "items": [{"item_id": item}], "fulfilment": "delivery"})
    assert r.status_code == 409 and "20 km" in r.json()["detail"]
    pickup = await client.post("/customer/orders/quote", headers=CUSTOMER, json={
        "restaurant_id": loc.id, "items": [{"item_id": item}], "fulfilment": "pickup"})
    assert pickup.status_code == 200 and pickup.json()["delivery_fee"] == "$0.00"


async def test_busy_alert_calls_in_on_call_drivers(client, session, demo_location_id, admin):
    loc = await _make_public(session, demo_location_id)
    await client.post("/fleet/drivers", headers=ADMIN, json={"email": "rider@example.com", "name": "Riley"})
    await client.get("/me", headers=DRIVER)  # claims the invite
    await client.post("/driver/availability", headers=DRIVER, json={"status": "on_call"})
    await client.put("/customer/profile", headers=CUSTOMER, json=HOME)
    item = await _item_id(session, loc.id, "Pepperoni Pizza")
    for _ in range(2):
        o = (await client.post("/customer/orders", headers=CUSTOMER, json={
            "restaurant_id": loc.id, "items": [{"item_id": item}], "fulfilment": "delivery"})).json()["order"]
        for status in ("accepted", "ready"):
            await client.post(f"/locations/{loc.id}/app-orders/{o['id']}/status", headers=OWNER,
                              json={"status": status})
    busy = await session.scalar(select(Alert).where(Alert.kind == "fleet_busy"))
    assert busy is not None and busy.user_id == "dev-rider@example.com" and "0 driver(s) on shift" in busy.body
    # The fleet dashboard suggests combining the two orders from the same restaurant.
    overview = (await client.get("/fleet/overview", headers=ADMIN)).json()
    assert overview["deliveries"]["waiting"] == 2 and overview["batching_suggestions"]


async def test_booking_shared_between_customer_and_restaurant(client, session, demo_location_id):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    loc = await _make_public(session, demo_location_id)
    when = (datetime.now(ZoneInfo("America/Vancouver")) + timedelta(days=3)).replace(hour=19, minute=0, second=0,
                                                                                      microsecond=0)
    r = await client.post("/customer/bookings", headers=CUSTOMER, json={
        "restaurant_id": loc.id, "party_size": 4, "starts_at": when.isoformat(), "notes": "Window seat"})
    assert r.status_code == 200, r.text
    booking = r.json()
    assert booking["status"] == "requested"
    day = (await client.get(f"/locations/{loc.id}/bookings?day={when.date().isoformat()}", headers=OWNER)).json()
    assert booking["id"] in [b["id"] for b in day["bookings"]]
    r = await client.patch(f"/locations/{loc.id}/bookings/{booking['id']}", headers=OWNER, json={"status": "confirmed"})
    assert r.status_code == 200
    titles = [a["title"] for a in (await client.get("/me/alerts", headers=CUSTOMER)).json()["alerts"]]
    assert f"Booking confirmed at {loc.name}" in titles
    # The demo has 12 tables with 44 seats: once they're booked, another party is turned away at that time.
    for _ in range(2):
        full = await client.post("/customer/bookings", headers=CUSTOMER, json={
            "restaurant_id": loc.id, "party_size": 20, "starts_at": when.isoformat()})
        assert full.status_code == 200
    over = await client.post("/customer/bookings", headers=CUSTOMER, json={
        "restaurant_id": loc.id, "party_size": 2, "starts_at": when.isoformat()})
    assert over.status_code == 409 and "fully booked" in over.json()["detail"]


async def test_customer_agent_proposes_order_on_personal_budget(client, session, demo_location_id, fake_llm):
    loc = await _make_public(session, demo_location_id)
    await client.put("/customer/profile", headers=CUSTOMER, json=HOME)
    salmon = await _item_id(session, loc.id, "Grilled Salmon")
    llm = fake_llm([
        tool_result("search_restaurants", {"query": "salmon", "fulfilment": "pickup"}),
        tool_result("propose_order", {"restaurant_id": loc.id, "items": [{"item_id": salmon}],
                                      "fulfilment": "pickup"}, call_id="toolu_2"),
        text_result("Demo Bistro's grilled salmon is ready to confirm: $37.80 for pickup."),
    ])
    r = await client.post("/agents/customer/chat", headers=CUSTOMER,
                          json={"message": "Something with salmon to pick up", "lat": 49.262, "lng": -123.103})
    events = parse_sse(r.text)
    assert events[0]["agent"] == "customer"
    action = next(e for e in events if e["type"] == "action")["proposal"]
    assert action["kind"] == "customer_order" and action["quote"]["total"] == "$37.80"
    assert "Customer agent" in llm.calls[0]["system"][0]["text"]
    assert "Coco" in llm.calls[0]["system"][1]["text"]  # default avatar name
    assert {t["name"] for t in llm.calls[0]["tools"]} >= {"search_restaurants", "propose_order", "get_nutrition"}
    search_result = llm.calls[1]["messages"][-1]["content"][0]["content"]
    assert loc.id in search_result and "Grilled Salmon" in search_result
    usage = (await session.scalars(select(AIUsage))).all()
    assert usage[-1].location_id is None and usage[-1].user_id == "dev-diner@example.com"
    assert usage[-1].agent == "customer"
    # Nothing was ordered without the customer's confirmation.
    assert (await client.get("/customer/orders", headers=CUSTOMER)).json()["orders"] == []


async def test_driver_assistant_is_for_invited_drivers(client, admin, fake_llm):
    fake_llm([text_result("unused")])
    assert (await client.post("/agents/driver/chat", headers=CUSTOMER, json={"message": "hi"})).status_code == 403
    await client.post("/fleet/drivers", headers=ADMIN, json={"email": "rider@example.com", "name": "Riley"})
    llm = fake_llm([tool_result("get_my_stats", {"period": "this_week"}), text_result("No deliveries yet this week.")])
    r = await client.post("/agents/driver/chat", headers=DRIVER, json={"message": "How am I doing this week?"})
    events = parse_sse(r.text)
    assert events[-1]["type"] == "done"
    assert "Driver Assistant" in llm.calls[0]["system"][0]["text"] and "Riley" in llm.calls[0]["system"][1]["text"]
    assert '"deliveries": 0' in llm.calls[1]["messages"][-1]["content"][0]["content"]


async def test_restaurant_manager_forecast_and_supplier_tools(client, session, demo_location_id, fake_llm):
    llm = fake_llm([
        tool_result("get_forecast", {"days": 3}),
        tool_result("propose_purchase_orders", {}, call_id="toolu_2"),
        text_result("Busy weekend ahead; I drafted an order to Harbour Dairy for you to approve."),
    ])
    r = await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                          json={"message": "How busy will we be and what should I order?"})
    events = parse_sse(r.text)
    actions = [e["proposal"] for e in events if e["type"] == "action"]
    assert [a["kind"] for a in actions] == ["purchase_order"] and actions[0]["supplier"].startswith("Harbour Dairy")
    assert '"prep_list_tomorrow"' in llm.calls[1]["messages"][-1]["content"][0]["content"]
    drafts = (await session.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "draft"))).all()
    assert {d.id for d in drafts} == {a["purchase_order_id"] for a in actions}


async def test_restaurant_profile_and_menu_details(client, session, demo_location_id):
    r = await client.put(f"/locations/{demo_location_id}/profile", headers=OWNER,
                         json={"opening_hours": {"mon": [["11:00", "21:00"]]}, "cuisine": "Italian"})
    assert r.status_code == 200 and r.json()["opening_hours"] == {"mon": [["11:00", "21:00"]]}
    assert (await client.put(f"/locations/{demo_location_id}/profile", headers=OWNER,
                             json={"opening_hours": {"funday": [["11:00", "21:00"]]}})).status_code == 422
    item = await _item_id(session, demo_location_id, "Caesar Salad")
    r = await client.patch(f"/locations/{demo_location_id}/items/{item}", headers=OWNER,
                           json={"calories": 410, "tags": ["Gluten Free"]})
    assert r.status_code == 200
    session.expire_all()
    salad = await session.get(Item, item)
    assert salad.calories == 410 and salad.tags == ["gluten_free"] and salad.low_stock_threshold is None


async def test_guest_removal_on_request(client, session, demo_location_id):
    guests = (await client.get(f"/locations/{demo_location_id}/guests?sort=visits", headers=OWNER)).json()
    assert guests["total"] == 8 and guests["guests"][0]["name"] == "Ben Harris"
    gid = guests["guests"][0]["id"]
    assert (await client.delete(f"/locations/{demo_location_id}/guests/{gid}", headers=INTRUDER)).status_code == 404
    assert (await client.delete(f"/locations/{demo_location_id}/guests/{gid}", headers=OWNER)).status_code == 200
    assert await session.get(Guest, gid) is None


async def test_nutrition_log_is_private(client, session):
    await client.post("/customer/nutrition", headers=CUSTOMER, json={"name": "Apple", "calories": 95})
    day = (await client.get("/customer/nutrition", headers=CUSTOMER)).json()
    assert day["totals"]["calories"] == 95
    entry = day["entries"][0]["id"]
    assert (await client.delete(f"/customer/nutrition/{entry}", headers=INTRUDER)).status_code == 404
    assert (await client.get("/customer/nutrition", headers=INTRUDER)).json()["entries"] == []
    assert await session.get(NutritionEntry, entry) is not None
