"""Opening Pass from Clover (the "Alternate Launch Path") and Clover launches that skip it."""

from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.models import Integration


def _where(r) -> tuple[str, dict]:
    assert r.status_code in (302, 303, 307), r.text
    url = urlparse(r.headers["location"])
    return url.path, {k: v[0] for k, v in parse_qs(url.query).items()}


async def test_launch_for_a_new_merchant_asks_to_connect_in_settings(client):
    path, q = _where(await client.get("/oauth/clover/launch?merchant_id=ABC123XYZ&client_id=app&employee_id=E1"))
    assert path == "/settings" and q == {"clover_launch": "1", "merchant_id": "ABC123XYZ"}
    # Odd merchant ids are dropped rather than passed on.
    _, q = _where(await client.get("/oauth/clover/launch?merchant_id=<script>"))
    assert q == {"clover_launch": "1"}


async def test_launch_for_a_linked_merchant_opens_pass(client, session, demo_location_id):
    session.add(Integration(location_id=demo_location_id, provider="clover", external_id="LINKED42", status="active"))
    await session.commit()
    r = await client.get("/oauth/clover/launch?merchant_id=LINKED42")
    assert r.headers["location"] == f"{get_settings().web_app_url}/"


async def test_clover_flows_that_skip_the_launch_path(client):
    # Authorized from Clover's side, then sent to our callback with no state: ask which restaurant.
    path, q = _where(await client.get("/oauth/clover/callback?code=c&merchant_id=M1&client_id=app"))
    assert path == "/settings" and q["clover_launch"] == "1" and q["merchant_id"] == "M1"
    # Sent to the Site URL itself.
    path, q = _where(await client.get("/?merchant_id=M2&client_id=app"))
    assert path == "/settings" and q["merchant_id"] == "M2"
    # A bare visit to the API's home is still "nothing here".
    assert (await client.get("/")).status_code == 404


async def test_connect_sandbox_with_a_test_token(client, session, demo_location_id, monkeypatch):
    from app.integrations.pos.base import POSAuthError
    from app.integrations.pos.clover import CloverAdapter
    from tests.conftest import INTRUDER, OWNER

    async def fake_name(self, token, merchant_id):
        if token != "good-token-123":
            raise POSAuthError("rejected")
        return "Test Merchant" if merchant_id == "PF2YPSMFAZYV1" else None

    monkeypatch.setattr(CloverAdapter, "merchant_name", fake_name)
    url = f"/locations/{demo_location_id}/integrations/clover/test-token"
    assert (await client.get("/integrations/options", headers=OWNER)).json() == {"clover_test_token": True}
    bad = await client.post(url, headers=OWNER, json={"merchant_id": "PF2YPSMFAZYV1", "token": "wrong-token"})
    assert bad.status_code == 422 and "didn't accept" in bad.json()["detail"]
    missing = await client.post(url, headers=OWNER, json={"merchant_id": "NOPE12345", "token": "good-token-123"})
    assert missing.status_code == 422 and "merchant ID" in missing.json()["detail"]
    assert (await client.post(url, headers=INTRUDER, json={"merchant_id": "PF2YPSMFAZYV1",
                                                           "token": "good-token-123"})).status_code == 404
    ok = await client.post(url, headers=OWNER, json={"merchant_id": "pf2ypsmfazyv1", "token": " good-token-123 "})
    assert ok.status_code == 200 and ok.json()["merchant"] == "Test Merchant"
    rows = (await client.get(f"/locations/{demo_location_id}/integrations", headers=OWNER)).json()
    clover = next(r for r in rows if r["provider"] == "clover")
    assert clover["status"] == "active" and clover["external_id"] == "PF2YPSMFAZYV1"

    # The stored token is used as is (it doesn't expire), never refreshed.
    from sqlalchemy import select

    from app.models import Integration
    from app.services.integration_store import access_token

    integ = await session.scalar(select(Integration).where(Integration.location_id == demo_location_id,
                                                           Integration.provider == "clover"))
    assert await access_token(session, integ) == "good-token-123"


async def test_test_tokens_are_refused_outside_the_sandbox(client, demo_location_id, monkeypatch):
    from tests.conftest import OWNER

    monkeypatch.setattr(get_settings(), "clover_api_base", "https://api.clover.com")
    r = await client.post(f"/locations/{demo_location_id}/integrations/clover/test-token", headers=OWNER,
                          json={"merchant_id": "PF2YPSMFAZYV1", "token": "good-token-123"})
    assert r.status_code == 403
    assert (await client.get("/integrations/options", headers=OWNER)).json() == {"clover_test_token": False}


async def test_missing_encryption_key_is_explained_not_dropped(client, demo_location_id, monkeypatch):
    """Without TOKEN_ENCRYPTION_KEY in production the token can't be stored: say so, with CORS headers."""
    from app import crypto
    from app.integrations.pos.clover import CloverAdapter
    from tests.conftest import OWNER

    async def fake_name(self, token, merchant_id):
        return "Test Merchant"

    monkeypatch.setattr(CloverAdapter, "merchant_name", fake_name)
    from types import SimpleNamespace

    # Production settings for the encryption key only (production also turns off the test sign-in).
    monkeypatch.setattr(crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key="", is_production=True))
    crypto._fernet.cache_clear()
    try:
        r = await client.post(f"/locations/{demo_location_id}/integrations/clover/test-token",
                              headers={**OWNER, "Origin": "http://localhost:5173"},
                              json={"merchant_id": "PF2YPSMFAZYV1", "token": "good-token-123"})
        assert r.status_code == 503 and "TOKEN_ENCRYPTION_KEY" in r.json()["detail"]
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        monkeypatch.setattr(crypto, "get_settings",
                            lambda: SimpleNamespace(token_encryption_key="not-a-key", is_production=True))
        crypto._fernet.cache_clear()
        r = await client.post(f"/locations/{demo_location_id}/integrations/clover/test-token", headers=OWNER,
                              json={"merchant_id": "PF2YPSMFAZYV1", "token": "good-token-123"})
        assert r.status_code == 503 and "isn't a valid key" in r.json()["detail"]
    finally:
        crypto._fernet.cache_clear()


async def test_unexpected_errors_still_reach_the_browser():
    """A crash comes back as a JSON 500 with CORS headers, so the app can show it (not "Failed to fetch")."""
    import httpx

    from app.main import create_app

    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as c:
        r = await c.get("/boom", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 500 and "went wrong" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_worker_with_a_different_key_says_so_in_settings(client, session, demo_location_id, monkeypatch):
    """The api saved the token with one key, the worker has another: the owner sees why syncing stopped."""
    from types import SimpleNamespace

    from cryptography.fernet import Fernet
    from sqlalchemy import select

    from app import crypto
    from app.integrations.pos.clover import CloverAdapter
    from app.services.handlers import handle_sync_pos
    from tests.conftest import OWNER

    async def fake_name(self, token, merchant_id):
        return "Test Merchant"

    monkeypatch.setattr(CloverAdapter, "merchant_name", fake_name)
    r = await client.post(f"/locations/{demo_location_id}/integrations/clover/test-token", headers=OWNER,
                          json={"merchant_id": "PF2YPSMFAZYV1", "token": "good-token-123"})
    assert r.status_code == 200
    other = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key=other, is_production=True))
    crypto._fernet.cache_clear()
    try:
        integ = await session.scalar(select(Integration).where(Integration.location_id == demo_location_id,
                                                               Integration.provider == "clover"))
        await handle_sync_pos(session, {"integration_id": integ.id})
    finally:
        crypto._fernet.cache_clear()
    rows = (await client.get(f"/locations/{demo_location_id}/integrations", headers=OWNER)).json()
    clover = next(r for r in rows if r["provider"] == "clover")
    assert "same key" in clover["last_error"]
