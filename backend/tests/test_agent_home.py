"""Each agent's home: Working on and Activity (built from existing records), memory, and what it can reach."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AgentMemory, Review
from tests.conftest import INTRUDER, OWNER, parse_sse, text_result, tool_result

CUSTOMER = {"Authorization": "Bearer dev:diner@example.com"}
DRIVER = {"Authorization": "Bearer dev:rider@example.com"}
ADMIN = {"Authorization": "Bearer dev:ops@example.com"}


def _ids(work: dict, state: str) -> list[str]:
    return [e["id"] for e in work[state]]


async def test_restaurant_work_shows_what_needs_you_and_what_happened(client, session, demo_location_id):
    loc = demo_location_id
    await client.post(f"/locations/{loc}/purchase-orders/draft", headers=OWNER)
    pos = (await client.get(f"/locations/{loc}/purchase-orders", headers=OWNER)).json()
    draft = next(p for p in pos if p["status"] == "draft" and p["supplier"].startswith("Harbour Dairy"))

    work = (await client.get(f"/locations/{loc}/work", headers=OWNER)).json()
    assert set(work) == {"needs_you", "in_progress", "scheduled", "done"}

    # The drafted supplier order waits for an OK, with the confirm card the chat uses.
    po = next(e for e in work["needs_you"] if e["id"] == f"po:{draft['id']}")
    assert po["proposal"]["kind"] == "purchase_order" and po["proposal"]["purchase_order_id"] == draft["id"]
    assert {ln["name"] for ln in po["proposal"]["lines"]} >= {"Tiramisu"}
    assert po["link"] == "/purchasing"

    # Low-rated reviews without a reply wait too; the demo's 1-star review is among them.
    one_star = await session.scalar(select(Review).where(Review.location_id == loc, Review.rating == 1))
    assert f"review:{one_star.id}" in _ids(work, "needs_you")

    # The morning brief is scheduled.
    assert "digest-next" in _ids(work, "scheduled")

    # Approve the order: it leaves "needs you", and the record says this person approved it.
    assert (await client.post(f"/locations/{loc}/purchase-orders/{draft['id']}/approve", headers=OWNER)).status_code == 200
    work = (await client.get(f"/locations/{loc}/work", headers=OWNER)).json()
    assert f"po:{draft['id']}" not in _ids(work, "needs_you")
    assert f"po:{draft['id']}" in _ids(work, "in_progress")  # waiting for the delivery
    approved = next(e for e in work["done"] if e["id"] == f"po-approved:{draft['id']}")
    assert approved["by_you"] is True

    # Other people's restaurants stay hidden.
    assert (await client.get(f"/locations/{loc}/work", headers=INTRUDER)).status_code == 404


async def test_remember_and_forget_through_chat_and_the_app(client, session, demo_location_id, fake_llm):
    loc = demo_location_id
    llm = fake_llm([
        tool_result("remember", {"fact": "Harbour Dairy delivers Tue, Thu and Sat"}),
        text_result("Got it, I'll remember that."),
    ])
    r = await client.post(f"/locations/{loc}/chat", headers=OWNER,
                          json={"message": "Remember Harbour delivers Tue, Thu, Sat"})
    events = parse_sse(r.text)
    assert {"type": "tool", "name": "remember"} in events
    assert {t["name"] for t in llm.calls[0]["tools"]} >= {"remember", "forget", "get_sales_summary"}
    assert "cache_control" in llm.calls[0]["tools"][-1]

    mems = (await client.get(f"/locations/{loc}/memory", headers=OWNER)).json()["memories"]
    assert [m["text"] for m in mems] == ["Harbour Dairy delivers Tue, Thu and Sat"]

    # Typed in the app as well; duplicates are ignored.
    await client.post(f"/locations/{loc}/memory", headers=OWNER, json={"text": "Keep labour under 30% of sales"})
    await client.post(f"/locations/{loc}/memory", headers=OWNER, json={"text": "keep labour under 30% of sales"})
    mems = (await client.get(f"/locations/{loc}/memory", headers=OWNER)).json()["memories"]
    assert len(mems) == 2

    # The next chat carries the notes in its context, with short ids the agent can forget by.
    harbour = next(m for m in mems if m["text"].startswith("Harbour"))
    llm = fake_llm([tool_result("forget", {"note_id": harbour["id"][:8]}), text_result("Forgotten.")])
    await client.post(f"/locations/{loc}/chat", headers=OWNER, json={"message": "Forget the Harbour days"})
    context = llm.calls[0]["system"][1]["text"]
    assert "Harbour Dairy delivers Tue, Thu and Sat" in context and harbour["id"][:8] in context
    mems = (await client.get(f"/locations/{loc}/memory", headers=OWNER)).json()["memories"]
    assert [m["text"] for m in mems] == ["Keep labour under 30% of sales"]

    # Forget from the app; nobody else can.
    other = mems[0]["id"]
    assert (await client.delete(f"/locations/{loc}/memory/{other}", headers=INTRUDER)).status_code == 404
    assert (await client.delete(f"/locations/{loc}/memory/{other}", headers=OWNER)).status_code == 200
    assert (await session.scalars(select(AgentMemory))).all() == []


async def test_restaurant_access_lists_connections_and_spend(client, demo_location_id):
    r = (await client.get(f"/locations/{demo_location_id}/agent-access", headers=OWNER)).json()
    providers = {c["provider"]: c for c in r["connections"]}
    assert set(providers) >= {"clover", "google"}
    assert providers["google"]["can"].startswith("Reads reviews")
    assert r["spend"]["budget_usd"] == get_settings().monthly_ai_budget_usd and r["spend"]["spent_usd"] == 0


async def test_customer_work_memory_and_goal(client):
    work = (await client.get("/agents/customer/work", headers=CUSTOMER)).json()
    assert work["in_progress"] == [] and work["goal"] is None

    await client.put("/customer/profile", headers=CUSTOMER, json={"protein_goal_g": 120})
    await client.post("/customer/nutrition", headers=CUSTOMER, json={"name": "Chicken bowl", "protein_g": 130})
    goal = (await client.get("/agents/customer/work", headers=CUSTOMER)).json()["goal"]
    assert goal["target"] == 120 and len(goal["days"]) == 7 and goal["met_days"] == 1

    m = (await client.post("/agents/customer/memory", headers=CUSTOMER, json={"text": "Vegetarian"})).json()
    mems = (await client.get("/agents/customer/memory", headers=CUSTOMER)).json()["memories"]
    assert [x["text"] for x in mems] == ["Vegetarian"]
    # A memory belongs to one person and one agent.
    assert (await client.delete(f"/agents/customer/memory/{m['id']}", headers=OWNER)).status_code == 404
    assert (await client.get("/agents/customer/memory", headers=OWNER)).json()["memories"] == []

    access = (await client.get("/agents/customer/access", headers=CUSTOMER)).json()
    assert access["connections"][0]["status"] == "test"  # no Stripe key in tests


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_admin_emails", "ops@example.com")


async def test_driver_work_is_for_drivers(client, admin):
    assert (await client.get("/agents/driver/work", headers=CUSTOMER)).status_code == 403
    await client.post("/fleet/drivers", headers=ADMIN, json={"email": "rider@example.com", "name": "Riley"})
    await client.get("/driver/me", headers=DRIVER)
    await client.post("/driver/shift/start", headers=DRIVER)
    work = (await client.get("/agents/driver/work", headers=DRIVER)).json()
    assert work["in_progress"][0]["kind"] == "shift"
    assert work["goal"]["earned_cents"] == 0
    assert (await client.get("/agents/nobody/work", headers=DRIVER)).status_code == 404
