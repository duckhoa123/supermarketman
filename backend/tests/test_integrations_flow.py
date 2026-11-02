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
