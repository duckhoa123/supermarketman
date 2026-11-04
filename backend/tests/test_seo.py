"""The website SEO plan: what every site gets, what the plan adds, the health check, billing and sitemaps."""

import hashlib
import hmac
import json
import time

import httpx
import respx

from app.config import get_settings
from app.services import seo, site_render, site_seo, site_templates as st
from tests.conftest import INTRUDER, OWNER


def _site(**kw) -> site_render.SiteData:
    menu = [("Mains", [site_render.MenuItem("Steak </script> Frites", "$34.00", "Hanger steak.", ["gluten_free"],
                                            id="1" * 36, price_value="34.00")])]
    return site_render.SiteData(
        name="Demo", cuisine="Italian", address="2210 W 4th Ave, Vancouver, BC V6K 1N8", phone="+16045550100",
        lat=49.2, lng=-123.1, hours={"mon": [["11:30", "22:00"]]}, menu=menu, book_url="https://app.example/?book=L",
        asset_base="https://api.example/sites/demo/assets", **kw)


def _content(**seo_in):
    c = st.clean_content(st.starter_content("bistro", "Demo", "Italian"))
    c["seo"].update(seo_in)
    if not any(s["type"] == "menu" for s in c["sections"]):
        c["sections"].append(st.new_section("menu"))
    return st.clean_content(c)


def test_clean_seo_keeps_only_safe_values():
    out = st.clean_seo({"keywords": ["Pizza  Kitsilano", "pizza kitsilano", 3, "x" * 99], "ga_id": "g-abc123",
                        "google_verify": 'abc"><script>', "bing_verify": "0123456789ABCDEF", "price_range": "$$$$$",
                        "alts": {"not-an-id": "x", "a" * 8 + "-aaaa-4aaa-8aaa-" + "a" * 12: "Pasta"}, "index": "no"})
    assert out["keywords"] == ["pizza kitsilano", "x" * 60]
    assert out["ga_id"] == "G-ABC123" and out["google_verify"] == "" and out["bing_verify"] == "0123456789ABCDEF"
    assert out["price_range"] == "" and out["index"] is True and list(out["alts"].values()) == ["Pasta"]


def test_every_site_gets_the_basics_and_the_plan_adds_the_rest():
    content = _content(google_verify="abcdefghij123", ga_id="G-TEST1234", price_range="$$")
    free = site_render.render(content, _site(), "https://demo.example/")
    assert '<link rel="canonical" href="https://demo.example/">' in free and 'og:title' in free
    assert "application/ld+json" not in free and "google-site-verification" not in free and "gtag" not in free

    pro = site_render.render(content, _site(seo_pro=True), "https://demo.example/")
    assert "google-site-verification" in pro and "googletagmanager.com/gtag/js?id=G-TEST1234" in pro
    raw = pro.split('<script type="application/ld+json">')[1].split("</script>")[0]
    assert "</script" not in raw  # a dish name can't close the script tag
    data = json.loads(raw)
    assert data["@type"] == "Restaurant" and data["priceRange"] == "$$" and data["servesCuisine"] == "Italian"
    assert data["address"] == {"@type": "PostalAddress", "streetAddress": "2210 W 4th Ave",
                               "addressLocality": "Vancouver", "addressCountry": "CA", "addressRegion": "BC",
                               "postalCode": "V6K 1N8"}
    assert data["openingHoursSpecification"][0] == {"@type": "OpeningHoursSpecification", "dayOfWeek": "Monday",
                                                    "opens": "11:30", "closes": "22:00"}
    item = data["hasMenu"]["hasMenuSection"][0]["hasMenuItem"][0]
    assert item["offers"] == {"@type": "Offer", "price": "34.00", "priceCurrency": "CAD"}
    assert item["suitableForDiet"] == ["https://schema.org/GlutenFreeDiet"] and data["acceptsReservations"] is True

    hidden = site_render.render(_content(index=False), _site(), "https://demo.example/")
    assert '<meta name="robots" content="noindex">' in hidden


def test_owner_photo_descriptions_replace_the_automatic_ones():
    pid = "12345678-1234-4234-8234-123456789abc"
    content = _content(alts={pid: "Hand-rolled tagliatelle with ragù"})
    gallery = st.new_section("gallery")
    gallery["props"]["images"] = [pid]
    content["sections"].append(gallery)
    html = site_render.render(st.clean_content(content), _site())
    assert 'alt="Hand-rolled tagliatelle with ragù"' in html
    photos = site_seo.page_photos(st.clean_content(content), _site())
    assert [p["needs_alt"] for p in photos if p["id"] == pid] == [False]


def test_audit_scores_and_orders_what_to_fix():
    bare = _content(title="", description="")
    out = site_seo.audit(bare, _site(), site_seo.Facts())
    first = [c["id"] for c in out["checks"][:3]]
    assert "published" in first and "title" in first and out["score"] < 60
    good = _content(title="Demo | Italian pasta and pizza in Vancouver",
                    description="Fresh pasta and wood-fired pizza on West 4th in Vancouver. See the menu and hours, "
                                "order online or book a table tonight.", keywords=["italian vancouver"])
    good["links"]["instagram"] = "https://instagram.com/demo"
    better = site_seo.audit(good, _site(seo_pro=True), site_seo.Facts(published=True, custom_domain=True,
                                                                       google_profile=True))
    assert better["score"] > out["score"] + 25
    assert better["keywords"] == [{"phrase": "italian vancouver", "title": True, "description": False,
                                   "headline": False, "page": True}]


def _stripe_sig(payload: bytes, secret: str) -> str:
    ts = str(int(time.time()))
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


async def test_plan_in_test_mode_unlocks_the_tools(client, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    out = (await client.get(f"{base}/seo", headers=OWNER)).json()
    assert out["plan"]["active"] is False and out["plan"]["billing"] == "test" and out["plan"]["price"] == "$19.00/month"
    assert 0 <= out["score"] <= 100 and out["checks"] and out["urls"]["sitemap"].endswith("/sites/demo-bistro/sitemap.xml")
    assert out["structured_data"]["name"] == "Demo Bistro"
    assert (await client.post(f"{base}/seo/suggest", headers=OWNER)).status_code == 402
    assert (await client.post(f"{base}/seo/upgrade", headers=INTRUDER)).status_code == 404

    assert (await client.post(f"{base}/seo/upgrade", headers=OWNER)).json() == {"status": "test"}
    tips = (await client.post(f"{base}/seo/suggest", headers=OWNER)).json()
    assert tips["ai"] is False and "Demo Bistro" in tips["title"] and tips["keywords"]
    assert "application/ld+json" in (await client.get(f"{base}/preview", headers=OWNER)).text
    assert (await client.get(f"{base}/seo", headers=OWNER)).json()["plan"]["active"] is True

    assert (await client.post(f"{base}/seo/cancel", headers=OWNER)).json()["active"] is False
    assert "application/ld+json" not in (await client.get(f"{base}/preview", headers=OWNER)).text


async def test_plan_with_stripe_checkout_and_webhooks(client, session, demo_location_id, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_webhook_secret", "whsec_x")
    base = f"/locations/{demo_location_id}/website"
    site_id = (await client.get(base, headers=OWNER)).json()["id"]
    with respx.mock(assert_all_called=False) as mock:
        create = mock.post("https://api.stripe.com/v1/checkout/sessions").mock(
            return_value=httpx.Response(200, json={"id": "cs_test_123456789", "url": "https://checkout.stripe.com/c/x"}))
        mock.get("https://api.stripe.com/v1/checkout/sessions/cs_test_123456789").mock(return_value=httpx.Response(
            200, json={"client_reference_id": site_id, "status": "complete", "subscription": "sub_1", "customer": "cus_1"}))
        cancel = mock.post("https://api.stripe.com/v1/subscriptions/sub_1").mock(return_value=httpx.Response(
            200, json={"id": "sub_1", "cancel_at": int(time.time()) + 86400 * 20}))
        r = (await client.post(f"{base}/seo/upgrade", headers=OWNER)).json()
        assert r == {"status": "checkout", "checkout_url": "https://checkout.stripe.com/c/x"}
        form = dict(httpx.QueryParams(create.calls[0].request.content.decode()))
        assert form["mode"] == "subscription" and form["line_items[0][price_data][unit_amount]"] == "1900"
        assert form["client_reference_id"] == site_id
        plan = (await client.post(f"{base}/seo/confirm", headers=OWNER, json={"session_id": "cs_test_123456789"})).json()
        assert plan["active"] and plan["status"] == "active"
        plan = (await client.post(f"{base}/seo/cancel", headers=OWNER)).json()
        assert cancel.called and plan["status"] == "canceling" and plan["active"]  # runs to the end of the month

    # Stripe says the subscription ended: the plan turns off. Unsigned or forged events are refused.
    event = json.dumps({"type": "customer.subscription.deleted",
                        "data": {"object": {"id": "sub_1", "metadata": {"website_id": site_id}}}}).encode()
    forged = await client.post("/webhooks/stripe", content=event, headers={"Stripe-Signature": "t=1,v1=bad"})
    assert forged.status_code == 400
    r = await client.post("/webhooks/stripe", content=event, headers={"Stripe-Signature": _stripe_sig(event, "whsec_x")})
    assert r.json() == {"ok": True, "handled": True}
    assert (await client.get(f"{base}/seo", headers=OWNER)).json()["plan"]["active"] is False


async def test_sitemaps_robots_and_indexnow(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    assert "demo-bistro" not in (await client.get("/sitemap.xml")).text  # not published yet
    await client.post(f"{base}/publish", headers=OWNER)
    xml = (await client.get("/sitemap.xml")).text
    assert "<loc>http://localhost:8000/sites/demo-bistro</loc>" in xml and "<lastmod>" in xml
    assert "/sites/demo-bistro</loc>" in (await client.get("/sites/demo-bistro/sitemap.xml")).text
    robots = (await client.get("/robots.txt")).text
    assert "Allow: /sites/\nDisallow: /" in robots and "Sitemap: http://localhost:8000/sitemap.xml" in robots

    await client.put(f"{base}/settings", headers=OWNER, json={"custom_domain": "www.demobistro.ca"})
    host = {"Host": "www.demobistro.ca"}
    assert "Allow: /\n" in (await client.get("/robots.txt", headers=host)).text
    assert "<loc>https://www.demobistro.ca</loc>" in (await client.get("/sitemap.xml", headers=host)).text

    # Hidden from search engines: left out of the sitemap.
    draft = (await client.get(base, headers=OWNER)).json()["draft"]
    draft["seo"]["index"] = False
    await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})
    await client.post(f"{base}/publish", headers=OWNER)
    assert "<url>" not in (await client.get("/sitemap.xml", headers=host)).text

    key = seo.indexnow_key()
    assert (await client.get(f"/{key}.txt")).text == key and (await client.get("/nope.txt")).status_code == 404
    from app.models import Website

    site = await session.get(Website, (await client.get(base, headers=OWNER)).json()["id"])
    with respx.mock() as mock:
        ping = mock.post("https://api.indexnow.org/indexnow").mock(return_value=httpx.Response(202))
        out = await seo.notify_search_engines(session, site, "https://www.demobistro.ca")
        assert out["ok"] and json.loads(ping.calls[0].request.content) == {
            "host": "www.demobistro.ca", "key": key, "keyLocation": f"https://www.demobistro.ca/{key}.txt",
            "urlList": ["https://www.demobistro.ca"]}
    assert (await seo.notify_search_engines(session, site, "http://localhost:8000/sites/x"))["ok"] is False


async def test_ai_writes_search_text_and_photo_descriptions(client, demo_location_id, fake_llm):
    from tests.conftest import text_result

    base = f"/locations/{demo_location_id}/website"
    await client.post(f"{base}/seo/upgrade", headers=OWNER)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ids = [(await client.post(f"{base}/images", headers=OWNER, files={"file": ("a.png", png, "image/png")})).json()["id"]
           for _ in range(2)]
    draft = (await client.get(base, headers=OWNER)).json()["draft"]
    gallery = st.new_section("gallery")
    gallery["props"]["images"] = ids
    draft["sections"].append(gallery)
    await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})
    photos = (await client.get(f"{base}/seo", headers=OWNER)).json()["photos"]
    assert [p["needs_alt"] for p in photos if p["id"] in ids] == [True, True]

    llm = fake_llm([text_result('{"title": "Demo Bistro | Italian in Vancouver", "description": "Fresh pasta.", '
                                '"keywords": ["Italian Vancouver", "pasta main street"]}'),
                    text_result('{"1": "Bowl of carbonara with black pepper", "2": "Tiramisu dusted with cocoa"}')])
    tips = (await client.post(f"{base}/seo/suggest", headers=OWNER)).json()
    assert tips == {"title": "Demo Bistro | Italian in Vancouver", "description": "Fresh pasta.",
                    "keywords": ["italian vancouver", "pasta main street"], "ai": True}
    assert "Demo Bistro" in llm.calls[0]["messages"][0]["content"]
    out = (await client.post(f"{base}/seo/describe-photos", headers=OWNER, json={"ids": ids + ["not-on-page"]})).json()
    assert out == {"alts": {ids[0]: "Bowl of carbonara with black pepper", ids[1]: "Tiramisu dusted with cocoa"}, "ai": True}
    blocks = llm.calls[1]["messages"][0]["content"]
    assert [b["type"] for b in blocks].count("image") == 2
