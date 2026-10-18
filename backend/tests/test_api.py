"""End-to-end tests through the HTTP API with a fake model and a fake Clover."""

from datetime import timedelta

import pytest

from sqlalchemy import func, select

from app.crypto import encrypt
from app.integrations.pos import clover_parse
from app.integrations.pos.base import NormalizedItem, NormalizedLine, NormalizedOrder
from app.integrations.pos.registry import set_adapter
from app.models import (
    AIUsage,
    Alert,
    DailySales,
    Integration,
    Item,
    Job,
    Location,
    NotificationDelivery,
    Order,
    Organization,
    Review,
    utcnow,
)
from app.services import alerts
from app.services.jobs import enqueue, run_pending
from tests.conftest import INTRUDER, OWNER, parse_sse, text_result, tool_result


async def test_health(client):
    assert (await client.get("/health")).json() == {"ok": True}


async def test_requires_auth(client):
    assert (await client.get("/me")).status_code == 401


async def test_onboarding_creates_location_and_rules(client):
    r = await client.post("/onboarding", headers=OWNER, json={"business_name": "Acme", "location_name": "Acme CBD"})
    assert r.status_code == 200, r.text
    loc_id = r.json()["id"]
    me = (await client.get("/me", headers=OWNER)).json()
    assert [x["id"] for x in me["locations"]] == [loc_id]
    rules = (await client.get(f"/locations/{loc_id}/alert-rules", headers=OWNER)).json()
    assert {r["kind"] for r in rules} == {"bad_review", "low_stock", "sales_drop", "daily_digest", "weekly_report",
                                         "monthly_report"}


async def test_tenant_isolation(client, demo_location_id):
    ok = await client.get(f"/locations/{demo_location_id}/dashboard", headers=OWNER)
    assert ok.status_code == 200
    for path in ("dashboard", "inventory", "reviews", "alerts", "orders"):
        r = await client.get(f"/locations/{demo_location_id}/{path}", headers=INTRUDER)
        assert r.status_code == 404, path


async def test_dashboard_has_numbers(client, demo_location_id):
    d = (await client.get(f"/locations/{demo_location_id}/dashboard?period=last_7_days", headers=OWNER)).json()
    assert d["summary"]["gross_cents"] > 0
    assert d["summary"]["orders"] > 0
    assert len(d["trend_28d"]) == 28
    assert d["top_items"]["rows"]
    assert {i["name"] for i in d["low_stock"]["items"]} >= {"Tiramisu", "Burrata & Tomato"}
    bad = await client.get(f"/locations/{demo_location_id}/dashboard?period=fortnight", headers=OWNER)
    assert bad.status_code == 422


async def test_chat_runs_tools_and_logs_cost(client, session, demo_location_id, fake_llm):
    llm = fake_llm([
        tool_result("get_sales_summary", {"period": "yesterday", "compare": "same_weekday_last_week"}),
        text_result("Yesterday you took $2,000 from 80 orders."),
    ])
    r = await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                          json={"message": "How were sales yesterday?"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "meta" and types[-1] == "done"
    assert {"type": "tool", "name": "get_sales_summary"} in events
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Yesterday you took $2,000 from 80 orders."
    assert events[0]["model"] == "claude-haiku-5-5"  # simple question stays on the cheap model

    # The second model call received the tool result with real numbers from the database.
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "user" and tool_msg["content"][0]["type"] == "tool_result"
    assert '"gross_cents"' in tool_msg["content"][0]["content"]
    # Static system prompt and tools are marked for caching.
    assert llm.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" in llm.calls[0]["tools"][-1]

    usage = (await session.scalars(select(AIUsage))).all()
    assert len(usage) == 1 and usage[0].cost_usd > 0 and usage[0].cache_read_tokens == 1500

    # A follow-up in the same conversation carries the earlier Q&A as plain text history.
    conv_id = events[0]["conversation_id"]
    llm2 = fake_llm([text_result("Saturday was busiest.")])
    await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                      json={"message": "Which day was busiest?", "conversation_id": conv_id})
    history = llm2.calls[0]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[0]["content"] == "How were sales yesterday?"


async def test_chat_deep_question_uses_stronger_model(client, demo_location_id, fake_llm):
    fake_llm([text_result("Because Tuesday lunch was quiet.")])
    r = await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                          json={"message": "Why were sales down three days ago?"})
    assert parse_sse(r.text)[0]["model"] == "claude-sonnet-5-5"


async def test_chat_proposal_for_review_reply(client, session, demo_location_id, fake_llm):
    review = await session.scalar(select(Review).where(Review.location_id == demo_location_id, Review.rating == 1))
    fake_llm([
        tool_result("propose_review_reply", {"review_id": review.id, "reply_text": "We're sorry, Alex."}),
        text_result("I've drafted a reply for you to approve."),
    ])
    r = await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                          json={"message": "Draft a reply to Alex's review"})
    events = parse_sse(r.text)
    action = next(e for e in events if e["type"] == "action")
    assert action["proposal"]["kind"] == "review_reply"
    assert events[-1]["proposals"][0]["review_id"] == review.id
    await session.refresh(review)
    assert review.suggested_reply == "We're sorry, Alex."
    assert review.reply_text is None  # nothing posted without approval


async def test_chat_tool_cannot_reach_other_tenant(client, session, demo_location_id, fake_llm):
    other = await client.post("/onboarding", headers=INTRUDER, json={"business_name": "X", "location_name": "X"})
    other_review = Review(location_id=other.json()["id"], source="google", external_id="x", rating=1,
                          posted_at=utcnow(), topics=[])
    session.add(other_review)
    await session.commit()
    llm = fake_llm([
        tool_result("propose_review_reply", {"review_id": other_review.id, "reply_text": "hi"}),
        text_result("I couldn't find that review."),
    ])
    await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER, json={"message": "reply"})
    tool_msg = llm.calls[1]["messages"][-1]["content"][0]
    assert tool_msg["is_error"] is True


async def test_chat_without_api_key_returns_503(client, demo_location_id):
    r = await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER, json={"message": "hi"})
    assert r.status_code == 503


class FakeClover:
    provider = "clover"

    def __init__(self, orders: dict, items: list | None = None):
        self.orders, self.items = orders, items or []

    async def refresh(self, refresh_token):
        raise AssertionError("token should still be valid")

    async def fetch_items(self, token, merchant_id):
        return self.items

    async def fetch_orders(self, token, merchant_id, since):
        return list(self.orders.values())

    async def fetch_order(self, token, merchant_id, order_id):
        return self.orders.get(order_id)

    async def fetch_item(self, token, merchant_id, item_id):
        return next((i for i in self.items if i.external_id == item_id), None)

    def parse_webhook(self, body):
        return clover_parse.parse_webhook(body)


async def _connect_clover(session, location_id: str) -> Integration:
    integ = Integration(location_id=location_id, provider="clover", external_id="M1",
                        access_token_enc=encrypt("tok"), refresh_token_enc=encrypt("ref"),
                        access_expires_at=utcnow() + timedelta(days=1), cursor={}, meta={})
    session.add(integ)
    await session.commit()
    return integ


async def test_clover_webhook_to_rollup(client, session, demo_location_id):
    await _connect_clover(session, demo_location_id)
    order = NormalizedOrder(
        external_id="CLV1", status="paid", total_cents=12345, opened_at=utcnow() - timedelta(minutes=5),
        lines=[NormalizedLine(name="Clover Special", qty=1, unit_price_cents=12345, total_cents=12345)],
    )
    set_adapter("clover", FakeClover({"CLV1": order}))
    location = await session.get(Location, demo_location_id)
    from app.services.analytics import local_today

    today = local_today(location)
    before = await session.scalar(
        select(DailySales.gross_cents).where(DailySales.location_id == demo_location_id, DailySales.day == today)
    ) or 0

    body = {"appId": "APP", "merchants": {"M1": [{"objectId": "O:CLV1", "type": "CREATE", "ts": 1}]}}
    assert (await client.post("/webhooks/clover", json=body, headers={"X-Clover-Auth": "wrong"})).status_code == 401
    assert (await client.post("/webhooks/clover", json={"verificationCode": "abc"})).status_code == 200
    r = await client.post("/webhooks/clover", json=body, headers={"X-Clover-Auth": "clover-secret"})
    assert r.status_code == 200

    assert await run_pending(time_budget_s=20) >= 1
    session.expire_all()
    stored = await session.scalar(select(Order).where(Order.external_id == "CLV1"))
    assert stored is not None and stored.total_cents == 12345
    after = await session.scalar(
        select(DailySales.gross_cents).where(DailySales.location_id == demo_location_id, DailySales.day == today)
    )
    assert after == before + 12345


async def test_full_sync_imports_items_and_orders(session, engine):
    from app.services.sync import sync_pos

    org = Organization(name="Org")
    session.add(org)
    await session.flush()
    loc = Location(org_id=org.id, name="New Place")
    session.add(loc)
    await session.commit()
    integ = await _connect_clover(session, loc.id)
    items = [NormalizedItem(external_id="I1", name="Latte", category="Coffee", price_cents=550, stock_qty=2)]
    orders = {
        f"O{i}": NormalizedOrder(external_id=f"O{i}", status="paid", total_cents=550,
                                 opened_at=utcnow() - timedelta(days=i),
                                 lines=[NormalizedLine("Latte", 1, 550, 550, item_external_id="I1")])
        for i in range(3)
    }
    set_adapter("clover", FakeClover(orders, items))
    result = await sync_pos(session, integ, full=True)
    assert result["orders"] == 3
    total = await session.scalar(select(func.sum(DailySales.gross_cents)).where(DailySales.location_id == loc.id))
    assert total == 1650
    line_cat = await session.scalar(select(Item.category).where(Item.location_id == loc.id))
    assert line_cat == "Coffee"
    await session.refresh(integ)
    assert integ.cursor.get("orders_modified_since") and integ.last_synced_at is not None


async def test_low_stock_alerts_are_deduplicated_and_delivered(session, demo_location_id):
    location = await session.get(Location, demo_location_id)
    first = await alerts.check_low_stock(session, location)
    second = await alerts.check_low_stock(session, location)
    assert first >= 2 and second == 0
    await run_pending(time_budget_s=20)
    session.expire_all()
    deliveries = (await session.scalars(select(NotificationDelivery))).all()
    # No providers are configured in tests, so every channel is recorded as skipped, never failed.
    assert deliveries and {d.status for d in deliveries} == {"skipped"}


async def test_digest(session, demo_location_id):
    location = await session.get(Location, demo_location_id)
    alert = await alerts.send_daily_digest(session, location)
    assert alert is not None and "Yesterday:" in alert.body
    assert await alerts.send_daily_digest(session, location) is None  # once a day


async def test_failed_job_is_retried_later(session, engine):
    job = await enqueue(session, "no_such_handler", {})
    job_id = job.id  # read before expire_all: expired attributes can't lazy-load in async code
    await session.commit()
    await run_pending(time_budget_s=5)
    session.expire_all()
    job = await session.get(Job, job_id)
    assert job.status == "queued" and job.attempts == 1 and "No handler" in job.last_error
    assert job.run_at > utcnow()


async def test_internal_endpoints_need_token(client, demo_location_id):
    assert (await client.post("/internal/tick")).status_code == 403
    r = await client.post("/internal/tick", headers={"X-Internal-Token": "test-internal"})
    assert r.status_code == 200


async def test_alerts_inbox(client, session, demo_location_id):
    location = await session.get(Location, demo_location_id)
    await alerts.check_low_stock(session, location)
    inbox = (await client.get(f"/locations/{demo_location_id}/alerts", headers=OWNER)).json()
    assert inbox["unread"] >= 2
    await client.post(f"/locations/{demo_location_id}/alerts/read-all", headers=OWNER)
    assert (await client.get(f"/locations/{demo_location_id}/alerts", headers=OWNER)).json()["unread"] == 0
    assert await session.scalar(select(func.count(Alert.id))) >= 2


async def test_table_status_update(client, demo_location_id):
    tables = (await client.get(f"/locations/{demo_location_id}/tables", headers=OWNER)).json()["tables"]
    t = tables[0]
    r = await client.patch(f"/locations/{demo_location_id}/tables/{t['id']}", headers=OWNER,
                           json={"status": "cleaning"})
    assert r.status_code == 200
    again = (await client.get(f"/locations/{demo_location_id}/tables", headers=OWNER)).json()["tables"]
    assert next(x for x in again if x["id"] == t["id"])["status"] == "cleaning"


async def test_worker_survives_errors_instead_of_crashing(engine, monkeypatch):
    """A failing tick (e.g. tables not created yet) must be logged and retried, not kill the process."""
    import app.worker as worker

    async def boom(session):
        raise RuntimeError('relation "integrations" does not exist')

    monkeypatch.setattr(worker, "schedule_periodic", boom)
    await worker.main(once=True, schedule=True)  # returns normally


async def test_demo_data_for_signed_in_account(client):
    me = (await client.get("/me", headers=OWNER)).json()
    assert me["demo_available"] is True and me["locations"] == []
    r = await client.post("/demo", headers=OWNER)
    assert r.status_code == 200, r.text
    loc_id = r.json()["id"]
    assert [x["id"] for x in (await client.get("/me", headers=OWNER)).json()["locations"]] == [loc_id]
    dash = (await client.get(f"/locations/{loc_id}/dashboard?period=last_7_days", headers=OWNER)).json()
    assert dash["summary"]["orders"] > 0
    assert (await client.post("/demo", headers=OWNER)).json()["id"] == loc_id  # once per account
    # Another user can't see it.
    assert (await client.get(f"/locations/{loc_id}/dashboard", headers=INTRUDER)).status_code == 404


def test_supabase_legacy_hs256_tokens(monkeypatch):
    import time

    import jwt as pyjwt
    from fastapi import HTTPException

    from app.auth import verify_token
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", "https://abc.supabase.co/")  # trailing slash tolerated
    claims = {"sub": "user-1", "email": "Owner@Example.com", "aud": "authenticated",
              "iss": "https://abc.supabase.co/auth/v1", "exp": int(time.time()) + 60}
    token = pyjwt.encode(claims, "legacy-secret", algorithm="HS256")

    monkeypatch.setattr(s, "supabase_jwt_secret", "")
    with pytest.raises(HTTPException) as missing:
        verify_token(token)
    assert missing.value.status_code == 500 and "SUPABASE_JWT_SECRET" in missing.value.detail

    monkeypatch.setattr(s, "supabase_jwt_secret", "legacy-secret")
    user = verify_token(token)
    assert (user.id, user.email) == ("user-1", "owner@example.com")

    with pytest.raises(HTTPException) as bad:
        verify_token(pyjwt.encode(claims, "wrong-secret", algorithm="HS256"))
    assert bad.value.status_code == 401
