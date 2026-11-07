"""The website SEO plan: billing (Stripe subscription, or a test switch without Stripe), the health check the
editor shows, AI-written titles, descriptions, search phrases and photo descriptions, and telling search engines
when the site changes (IndexNow, used by Bing, Yandex, Naver, Seznam and Yep).

Without STRIPE_SECRET_KEY nothing is charged: "Upgrade" turns the plan on as a test so the whole flow can be
tried. With a key, the owner pays on Stripe Checkout; the plan turns on when they come back (checked with
Stripe) or when Stripe's webhook says so, and turns off when Stripe says the subscription ended.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.config import get_settings
from app.models import Integration, Location, SiteAsset, Website, utcnow
from app.money import fmt_money
from app.services import site_render, site_seo, site_templates as st
from app.services.notify import http

log = logging.getLogger(__name__)

STRIPE = "https://api.stripe.com/v1"
INDEXNOW = "https://api.indexnow.org/indexnow"
BIG_PHOTO = 1_000_000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "testserver")

FEATURES = [
    ("schema", "Business details Google can read", "Your address, hours, menu and prices, in the format Google uses "
               "for Maps and rich results."),
    ("google", "Google preview and AI writer", "See your listing as it shows on Google, and get a title and "
               "description written for local searches."),
    ("keywords", "Search phrases", "Pick the phrases you want to be found for and see where each one appears."),
    ("alt", "Photo descriptions", "AI describes your photos so they show up in Google Images."),
    ("share", "Share picture", "Pick the photo shown when your link is shared on Facebook, WhatsApp or iMessage."),
    ("search", "Search Console, Bing and Analytics", "Verify your site with Google and Bing, add Google Analytics, "
               "and tell search engines each time you publish."),
]


class SeoError(ValueError):
    pass


class NeedsPlan(PermissionError):
    pass


# --- Plan ------------------------------------------------------------------------------------------------------


def plan_of(site: Website) -> dict:
    return dict(site.seo_plan or {})


def _set(site: Website, **changes) -> None:
    site.seo_plan = {**plan_of(site), **changes}  # a new dict, so the JSON column is saved


def is_active(site: Website) -> bool:
    p = plan_of(site)
    if p.get("status") in ("active", "test"):
        return True
    if p.get("status") == "canceling" and p.get("ends_at"):
        return datetime.fromisoformat(p["ends_at"]) > utcnow()
    return False


def require(site: Website) -> None:
    if not is_active(site):
        raise NeedsPlan("This is part of the SEO plan")


def price_label() -> str:
    s = get_settings()
    return f"{fmt_money(s.seo_price_cents, s.seo_currency)}/month"


def plan_out(site: Website) -> dict:
    p = plan_of(site)
    status = p.get("status", "off")
    if status == "canceling" and not is_active(site):
        status = "off"
    return {"active": is_active(site), "status": status, "since": p.get("since"), "ends_at": p.get("ends_at"),
            "price": price_label(), "billing": "stripe" if get_settings().stripe_secret_key else "test",
            "features": [{"id": i, "title": t, "text": x} for i, t, x in FEATURES]}


def activate(site: Website, subscription: str | None = None, customer: str | None = None) -> None:
    _set(site, status="active" if subscription else "test", since=utcnow().isoformat(), ends_at=None,
         subscription=subscription, customer=customer)


async def _stripe(method: str, path: str, form: dict | None = None) -> dict:
    resp = await http().request(method, f"{STRIPE}{path}", data=form, auth=(get_settings().stripe_secret_key, ""))
    if resp.status_code >= 400:
        log.warning("Stripe %s %s failed: %s", method, path, resp.text[:300])
        raise SeoError("The payment service didn't accept that. Please try again.")
    return resp.json()


async def upgrade(session: AsyncSession, location: Location, site: Website, email: str | None) -> dict:
    """Start the plan. Test mode: on at once. Stripe: returns the Checkout page to send the owner to."""
    if is_active(site) and plan_of(site).get("status") != "canceling":
        return {"status": "active"}
    s = get_settings()
    if not s.stripe_secret_key:
        activate(site)
        await session.commit()
        return {"status": "test"}
    if plan_of(site).get("status") == "canceling" and plan_of(site).get("subscription"):
        return await resume(session, site)
    web = s.web_app_url.rstrip("/")
    form = {
        "mode": "subscription", "client_reference_id": site.id, "metadata[website_id]": site.id,
        "subscription_data[metadata][website_id]": site.id, "line_items[0][quantity]": "1",
        "success_url": f"{web}/website?seo=success&seo_session={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{web}/website?seo=cancelled", "allow_promotion_codes": "true",
    }
    if email:
        form["customer_email"] = email
    if s.stripe_seo_price_id:
        form["line_items[0][price]"] = s.stripe_seo_price_id
    else:
        form |= {"line_items[0][price_data][currency]": s.seo_currency.lower(),
                 "line_items[0][price_data][unit_amount]": str(s.seo_price_cents),
                 "line_items[0][price_data][recurring][interval]": "month",
                 "line_items[0][price_data][product_data][name]": f"Pass SEO plan: {location.name}"}
    body = await _stripe("POST", "/checkout/sessions", form)
    return {"status": "checkout", "checkout_url": body["url"]}


async def confirm(session: AsyncSession, site: Website, checkout_id: str) -> dict:
    """Back from Stripe Checkout: turn the plan on if Stripe says it was paid for this site."""
    if not re.match(r"^cs_[A-Za-z0-9_]{8,200}$", checkout_id or ""):
        raise SeoError("Unknown checkout")
    if not get_settings().stripe_secret_key:
        return plan_out(site)
    body = await _stripe("GET", f"/checkout/sessions/{checkout_id}")
    if body.get("client_reference_id") != site.id:
        raise SeoError("That checkout was for another website")
    if body.get("status") == "complete":
        activate(site, body.get("subscription"), body.get("customer"))
        await session.commit()
    return plan_out(site)


def _period_end(sub: dict) -> str | None:
    ts = sub.get("cancel_at") or sub.get("current_period_end")
    if not ts:
        items = (sub.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


async def cancel(session: AsyncSession, site: Website) -> dict:
    """Stop the plan. With Stripe it runs to the end of the month already paid for."""
    p = plan_of(site)
    if p.get("subscription") and get_settings().stripe_secret_key:
        sub = await _stripe("POST", f"/subscriptions/{p['subscription']}", {"cancel_at_period_end": "true"})
        ends = _period_end(sub) or (utcnow() + timedelta(days=1)).isoformat()
        _set(site, status="canceling", ends_at=ends)
    else:
        _set(site, status="off", ends_at=None)
    await session.commit()
    return plan_out(site)


async def resume(session: AsyncSession, site: Website) -> dict:
    p = plan_of(site)
    if p.get("status") != "canceling" or not p.get("subscription"):
        raise SeoError("There's nothing to resume")
    await _stripe("POST", f"/subscriptions/{p['subscription']}", {"cancel_at_period_end": "false"})
    _set(site, status="active", ends_at=None)
    await session.commit()
    return {"status": "active"}


def verify_stripe(payload: bytes, header: str | None, secret: str, tolerance: int = 300) -> dict:
    """Check Stripe's signature on a webhook (HMAC-SHA256 over "timestamp.body")."""
    if not secret or not header:
        raise SeoError("Unsigned")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    ts = parts.get("t", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > tolerance:
        raise SeoError("Stale signature")
    want = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(want, s) for s in sigs):
        raise SeoError("Bad signature")
    return json.loads(payload)


async def stripe_event(session: AsyncSession, event: dict) -> bool:
    """Apply a Stripe event to the website it's for. Returns whether it was one of ours."""
    obj = (event.get("data") or {}).get("object") or {}
    site_id = (obj.get("metadata") or {}).get("website_id") or obj.get("client_reference_id")
    site = await session.get(Website, site_id) if site_id else None
    if site is None:
        return False
    kind = event.get("type")
    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        activate(site, obj.get("subscription"), obj.get("customer"))
    elif kind == "customer.subscription.deleted":
        _set(site, status="off", ends_at=None)
    elif kind == "customer.subscription.updated":
        status = obj.get("status")
        if status in ("canceled", "unpaid", "incomplete_expired"):
            _set(site, status="off", ends_at=None)
        elif status in ("active", "trialing", "past_due"):
            if obj.get("cancel_at_period_end"):
                _set(site, status="canceling", ends_at=_period_end(obj), subscription=obj.get("id"))
            else:
                _set(site, status="active", ends_at=None, subscription=obj.get("id"))
    else:
        return False
    await session.commit()
    return True


# --- Telling search engines -------------------------------------------------------------------------------------


def indexnow_key() -> str:
    s = get_settings()
    return s.indexnow_key or hashlib.sha256(f"indexnow:{s.app_base_url}".encode()).hexdigest()[:32]


async def notify_search_engines(session: AsyncSession, site: Website, page_url: str) -> dict:
    """IndexNow: one call tells Bing and the other IndexNow engines the page changed. (Google reads the
    sitemap instead; it has no such call.)"""
    u = urlparse(page_url)
    if not u.hostname or u.hostname in LOCAL_HOSTS or u.hostname.endswith(".local"):
        return {"ok": False, "message": "Search engines can only be told about a site on a public address."}
    key = indexnow_key()
    try:
        resp = await http().post(INDEXNOW, json={"host": u.hostname, "key": key,
                                                 "keyLocation": f"{u.scheme}://{u.hostname}/{key}.txt",
                                                 "urlList": [page_url]}, timeout=8.0)
        ok = resp.status_code in (200, 202)
        result = "ok" if ok else f"error {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 - never block publishing on this
        log.warning("IndexNow failed: %s", exc)
        ok, result = False, "unreachable"
    _set(site, pinged_at=utcnow().isoformat(), ping=result)
    await session.commit()
    return {"ok": ok, "message": "Search engines were told about your changes." if ok
            else "Search engines couldn't be reached; we'll try again next time you publish."}


# --- What the editor shows -----------------------------------------------------------------------------------------


def urls(site: Website, page_url: str) -> dict:
    base = get_settings().app_base_url.rstrip("/")
    if site.custom_domain:
        sitemap, robots = f"https://{site.custom_domain}/sitemap.xml", f"https://{site.custom_domain}/robots.txt"
    else:
        sitemap, robots = f"{base}/sites/{site.slug}/sitemap.xml", f"{base}/robots.txt"
    q = quote(page_url, safe="")
    return {"page": page_url, "sitemap": sitemap, "robots": robots,
            "rich_results": f"https://search.google.com/test/rich-results?url={q}",
            "pagespeed": f"https://pagespeed.web.dev/analysis?url={q}",
            "search_console": "https://search.google.com/search-console/welcome",
            "bing": "https://www.bing.com/webmasters/about",
            "business_profile": "https://business.google.com/",
            "analytics": "https://analytics.google.com/"}


async def overview(session: AsyncSession, location: Location, site: Website) -> dict:
    """Score, checks, search phrases, photos and links for the SEO panel, all from the draft."""
    from app.services import website

    data = await website.site_data(session, location, site, preview=True)
    data.seo_pro = is_active(site)
    content = st.clean_content(site.draft)
    data.alts = content["seo"]["alts"]
    page_url = website.public_url(site)
    google = await session.scalar(select(Integration.status).where(Integration.location_id == location.id,
                                                                   Integration.provider == "google"))
    photo_ids = [p["id"] for p in site_seo.page_photos(content, data)]
    sizes = dict((await session.execute(select(SiteAsset.id, SiteAsset.size).where(SiteAsset.id.in_(photo_ids))))
                 .all()) if photo_ids else {}
    facts = site_seo.Facts(published=site.published is not None, custom_domain=bool(site.custom_domain),
                           google_profile=google == "active",
                           big_photos=sum(1 for i in photo_ids if (sizes.get(i) or 0) > BIG_PHOTO))
    result = site_seo.audit(content, data, facts)
    for p in result["photos"]:
        p["url"] = f"/sites/{site.slug}/assets/{p['id']}"
        p["size"] = sizes.get(p["id"])
    anchors = {"menu"} if any(s["type"] == "menu" and s["visible"] for s in content["sections"]) else set()
    plan = plan_of(site)
    return {
        "plan": plan_out(site), **result, "city": site_seo.city_of(location.address),
        "cuisine": location.cuisine, "name": location.name, "urls": urls(site, page_url),
        "structured_data": site_seo.structured_data(
            content, data, page_url, anchors,
            site_render.menu_page_url(page_url) if st.menu_page_on(content) else None),
        "indexnow": {"pinged_at": plan.get("pinged_at"), "result": plan.get("ping")},
        "share_image": site_seo.share_image(content),
    }


# --- AI help ---------------------------------------------------------------------------------------------------

SUGGEST_SYSTEM = """You are a local SEO copywriter for an independent restaurant in Canada. Using only the facts \
given (never invent awards, history, prices or dishes), return ONLY a JSON object with:
"title": a page title of 45-60 characters with the restaurant's name, what it serves and its neighbourhood or city;
"description": 130-155 characters, specific and inviting, naming the area and one or two real dishes; no clichés, \
no emoji, no ALL CAPS;
"keywords": 6 lowercase search phrases of 2-4 words that guests nearby would really type, like "ramen kitsilano" \
or "late night pizza vancouver": the cuisine with the neighbourhood and with the city, and dishes they sell."""

ALT_SYSTEM = """You write alt text for photos on a restaurant's website, for Google Images and screen readers. \
For each photo say plainly what is in it, in under 15 words (at most 120 characters). Don't start with "image of" \
or "photo of". Use the dish or restaurant name given if it fits what you see; never invent ingredients you can't \
see. Return ONLY a JSON object mapping each photo's number (as a string) to its text."""


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


async def _facts(session: AsyncSession, location: Location) -> tuple[str, list[str], list[str]]:
    from sqlalchemy import func

    from app.models import Item, ItemSalesDaily

    top = [n for n, in await session.execute(
        select(ItemSalesDaily.name).where(ItemSalesDaily.location_id == location.id)
        .group_by(ItemSalesDaily.name).order_by(func.sum(ItemSalesDaily.qty).desc()).limit(6))]
    cats = [c for c, in await session.execute(select(Item.category).where(
        Item.location_id == location.id, Item.active.is_(True), Item.category.is_not(None)).distinct().limit(12))]
    return site_seo.city_of(location.address), top, cats


async def suggest(session: AsyncSession, location: Location, site: Website) -> dict:
    """A title, a description and search phrases written for local search."""
    require(site)
    city, top, cats = await _facts(session, location)
    area = (site.draft.get("seo") or {}).get("area") or ""
    place = area or city or "Vancouver"
    cuisine = (location.cuisine or "restaurant").strip()
    c = cuisine.lower()
    title = f"{location.name} | {cuisine} in {place}"
    fallback = {
        "title": title[:60],
        "description": (f"{location.name} serves {c} in {place}"
                        + (f": {', '.join(top[:2])} and more" if top else "")
                        + ". See the menu and hours, order online or book a table.")[:160],
        "keywords": list(dict.fromkeys(k for k in [
            f"{c} {place}".lower(), f"{c} {city}".lower() if city and city != place else "",
            f"{c} restaurant {city or place}".lower(), f"{top[0]} {place}".lower() if top else "",
            f"{location.name}".lower()] if k))[:6],
        "ai": False,
    }
    from app.agent.llm import LLMNotConfigured, get_llm
    from app.agent.runner import record_usage

    try:
        llm = get_llm()
    except LLMNotConfigured:
        return fallback
    facts = (f"Name: {location.name}\nCuisine: {location.cuisine or 'not given'}\nNeighbourhood: {area or 'not given'}\n"
             f"City: {city or 'not given'}\nAddress: {location.address or 'not given'}\n"
             f"Best sellers: {', '.join(top) or 'not known'}\nMenu sections: {', '.join(cats) or 'not known'}")
    s = get_settings()
    out = await llm.complete(model=s.model_default, system=[{"type": "text", "text": SUGGEST_SYSTEM}],
                             messages=[{"role": "user", "content": facts}], max_tokens=500)
    await record_usage(session, location.id, "website_seo", s.model_default, out.usage)
    await session.commit()
    data = _json(out.text)
    words = st.clean_seo({"keywords": data.get("keywords") if isinstance(data.get("keywords"), list) else []})
    return {"title": str(data.get("title") or fallback["title"])[:70],
            "description": str(data.get("description") or fallback["description"])[:160],
            "keywords": words["keywords"][:8] or fallback["keywords"], "ai": True}


async def describe_photos(session: AsyncSession, location: Location, site: Website, ids: list[str]) -> dict:
    """Suggested descriptions for up to 6 photos on the page (the owner applies them, so they can undo)."""
    require(site)
    from app.services import website

    data = await website.site_data(session, location, site, preview=True)
    photos = {p["id"]: p for p in site_seo.page_photos(st.clean_content(site.draft), data)}
    ids = [i for i in dict.fromkeys(ids) if i in photos][:6]
    if not ids:
        return {"alts": {}, "ai": False}

    def plain(p: dict) -> str:
        if p["auto_alt"] and p["auto_alt"] not in (location.name, f"{location.name} photo"):
            return p["auto_alt"][:120]
        what = (location.cuisine or "Food").strip()
        return {"Logo": f"{location.name} logo"}.get(p["where"], f"{what} at {location.name}")[:120]

    fallback = {"alts": {i: plain(photos[i]) for i in ids}, "ai": False}
    from app.agent.llm import LLMNotConfigured, get_llm
    from app.agent.runner import record_usage

    try:
        llm = get_llm()
    except LLMNotConfigured:
        return fallback
    rows = {a.id: a for a in await session.scalars(select(SiteAsset).options(undefer(SiteAsset.data)).where(
        SiteAsset.location_id == location.id, SiteAsset.id.in_(ids)))}
    blocks: list[dict] = [{"type": "text", "text": f"Restaurant: {location.name} ({location.cuisine or 'restaurant'})"}]
    order = []
    for i in ids:
        a = rows.get(i)
        if a is None:
            continue
        order.append(i)
        p = photos[i]
        blocks.append({"type": "text", "text": f"Photo {len(order)}: in the “{p['where']}” section"
                       + (f", shown as “{p['auto_alt']}”" if p["auto_alt"] else "")})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": a.content_type,
                                                   "data": base64.standard_b64encode(a.data).decode()}})
    s = get_settings()
    out = await llm.complete(model=s.model_default, system=[{"type": "text", "text": ALT_SYSTEM}],
                             messages=[{"role": "user", "content": blocks}], max_tokens=600)
    await record_usage(session, location.id, "website_seo", s.model_default, out.usage)
    await session.commit()
    got = _json(out.text)
    alts = {i: (str(got.get(str(n + 1)) or "").strip()[:125] or fallback["alts"][i]) for n, i in enumerate(order)}
    return {"alts": alts, "ai": True}


# --- Public: sitemap -------------------------------------------------------------------------------------------


def sitemap_entry(site: Website, page_url: str, asset_base: str) -> site_seo.SitemapEntry:
    content = site.published or {}
    images = [f"{asset_base}/{i}" for i in st.image_ids(content)]
    day = site.published_at.date().isoformat() if site.published_at else None
    return site_seo.SitemapEntry(page_url, day, images)


def sitemap_entries(site: Website, page_url: str, asset_base: str) -> list[site_seo.SitemapEntry]:
    """The home page, and the menu page when it's on."""
    home = sitemap_entry(site, page_url, asset_base)
    if not st.menu_page_on(site.published):
        return [home]
    return [home, site_seo.SitemapEntry(site_render.menu_page_url(page_url), home.lastmod)]


def listed(site: Website) -> bool:
    """Published and not hidden from search engines."""
    return site.published is not None and (site.published.get("seo") or {}).get("index", True) is not False
