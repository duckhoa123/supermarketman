"""Restaurant websites: the owner's editor (draft, preview, publish, history, images) and the public site."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import Location, SiteAsset, Website, WebsiteVersion
from app.config import get_settings
from app.services import seo, site_render, site_seo, site_templates, website
from app.tenancy import require_location, require_manager, require_owner

router = APIRouter(prefix="/locations/{location_id}/website", tags=["website"])
public = APIRouter(tags=["website-public"])


def _422(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("")
async def get_site(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    images = list(await session.scalars(select(SiteAsset.id).where(SiteAsset.location_id == location.id)
                                        .order_by(SiteAsset.created_at.desc())))
    # Image paths are relative to the API address.
    return {**website.site_out(site), **website.catalog(),
            "images": [{"id": i, "url": f"/sites/{site.slug}/assets/{i}"} for i in images],
            "menu_items": await website.menu_choices(session, location),
            "asset_base": f"/sites/{site.slug}/assets"}


class DraftIn(BaseModel):
    content: dict


@router.put("/draft")
async def save_draft(body: DraftIn, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        await website.save_draft(session, location, site, body.content)
    except ValueError as exc:
        raise _422(exc) from exc
    return website.site_out(site)


class TemplateIn(BaseModel):
    template: str
    start_fresh: bool = False  # True: replace the words and sections with the template's starter page


@router.post("/template")
async def choose_template(body: TemplateIn, location: Location = Depends(require_manager),
                          session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    if body.template not in site_templates.TEMPLATES:
        raise HTTPException(422, "Unknown template")
    content = (site_templates.starter_content(body.template, location.name, location.cuisine) if body.start_fresh
               else site_templates.apply_template(site.draft, body.template, location.name, location.cuisine))
    await website.save_draft(session, location, site, content)
    return website.site_out(site)


@router.get("/preview", response_class=HTMLResponse)
async def preview(edit: bool = False, page: str = "home", location: Location = Depends(require_location),
                  session: AsyncSession = Depends(get_session)):
    """The draft as it would look once published (only for the restaurant's team). edit=1 adds the markers the
    visual editor uses (which text and photos can be clicked, each section's id). page=menu: the menu page."""
    if page not in site_render.PAGES:
        raise HTTPException(422, "Unknown page")
    site = await website.get_or_create(session, location)
    html = await website.render_page(session, site, preview=True, edit=edit, page=page)
    return HTMLResponse(html or site_render.not_found_page(), headers={"Cache-Control": "no-store"})


@router.post("/publish")
async def publish(location: Location = Depends(require_manager), user: AuthUser = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    await website.publish(session, site, user.id)
    out = website.site_out(site)
    if seo.is_active(site) and seo.listed(site):
        out["search_engines"] = await seo.notify_search_engines(session, site, website.public_url(site))
    return out


@router.post("/discard")
async def discard(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Throw away unpublished edits: the draft goes back to what's live."""
    site = await website.get_or_create(session, location)
    if site.published is None:
        raise HTTPException(409, "Nothing is published yet")
    site.draft = site.published
    await session.commit()
    return website.site_out(site)


@router.post("/unpublish")
async def unpublish(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    site.published, site.published_at = None, None
    await session.commit()
    return website.site_out(site)


@router.get("/versions")
async def versions(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    rows = await session.scalars(select(WebsiteVersion).where(WebsiteVersion.website_id == site.id)
                                 .order_by(WebsiteVersion.created_at.desc()))
    return [{"id": v.id, "created_at": v.created_at.isoformat(), "template": v.content.get("template"),
             "live": v.content == site.published} for v in rows]


@router.post("/versions/{version_id}/restore")
async def restore(version_id: str, location: Location = Depends(require_manager),
                  session: AsyncSession = Depends(get_session)):
    """Load an earlier published version into the draft, to check and publish again."""
    site = await website.get_or_create(session, location)
    v = await session.get(WebsiteVersion, version_id)
    if v is None or v.website_id != site.id:
        raise HTTPException(404, "Version not found")
    try:
        await website.save_draft(session, location, site, v.content)
    except ValueError as exc:
        raise _422(exc) from exc
    return website.site_out(site)


class SettingsIn(BaseModel):
    slug: str | None = Field(default=None, max_length=60)
    custom_domain: str | None = Field(default=None, max_length=253)
    clear_domain: bool = False


@router.put("/settings")
async def settings(body: SettingsIn, location: Location = Depends(require_manager),
                   session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        await website.update_settings(session, site, body.slug, body.custom_domain, body.clear_domain)
    except ValueError as exc:
        raise _422(exc) from exc
    return website.site_out(site)


@router.post("/images")
async def upload_image(file: UploadFile = File(...), location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    data = await file.read(website.MAX_IMAGE_BYTES + 1)
    try:
        asset = await website.add_image(session, location, file.content_type, data)
    except ValueError as exc:
        raise _422(exc) from exc
    return {"id": asset.id, "url": f"/sites/{site.slug}/assets/{asset.id}"}


@router.delete("/images/{asset_id}")
async def delete_image(asset_id: str, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    asset = await session.get(SiteAsset, asset_id)
    if asset is None or asset.location_id != location.id:
        raise HTTPException(404, "Image not found")
    if asset_id in site_templates.image_ids(site.draft) | site_templates.image_ids(site.published or {}):
        raise HTTPException(409, "This image is used on the page; remove it there first")
    await session.delete(asset)
    await session.commit()
    return {"ok": True}


@router.post("/write-copy")
async def write_copy(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Suggested tagline, 'our story' and search description, written from the restaurant's own data."""
    return await website.write_copy(session, location)


# --- SEO plan ------------------------------------------------------------------------------------------------


def _seo_error(exc: Exception) -> HTTPException:
    if isinstance(exc, seo.NeedsPlan):
        return HTTPException(402, str(exc))
    return HTTPException(422, str(exc))


@router.get("/seo")
async def seo_overview(location: Location = Depends(require_location), session: AsyncSession = Depends(get_session)):
    """The SEO panel: plan, score and checks, search phrases, photos, links and what Google will read."""
    site = await website.get_or_create(session, location)
    return await seo.overview(session, location, site)


@router.post("/seo/upgrade")
async def seo_upgrade(location: Location = Depends(require_owner), user: AuthUser = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        return await seo.upgrade(session, location, site, user.email)
    except ValueError as exc:
        raise _seo_error(exc) from exc


class ConfirmIn(BaseModel):
    session_id: str = Field(max_length=255)


@router.post("/seo/confirm")
async def seo_confirm(body: ConfirmIn, location: Location = Depends(require_owner),
                      session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        return await seo.confirm(session, site, body.session_id)
    except ValueError as exc:
        raise _seo_error(exc) from exc


@router.post("/seo/cancel")
async def seo_cancel(location: Location = Depends(require_owner), session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        return await seo.cancel(session, site)
    except ValueError as exc:
        raise _seo_error(exc) from exc


@router.post("/seo/suggest")
async def seo_suggest(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """A title, description and search phrases written for local search (SEO plan)."""
    site = await website.get_or_create(session, location)
    try:
        return await seo.suggest(session, location, site)
    except (ValueError, PermissionError) as exc:
        raise _seo_error(exc) from exc


class PhotosIn(BaseModel):
    ids: list[str] = Field(max_length=20)


@router.post("/seo/describe-photos")
async def seo_describe(body: PhotosIn, location: Location = Depends(require_manager),
                       session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    try:
        return await seo.describe_photos(session, location, site, body.ids)
    except (ValueError, PermissionError) as exc:
        raise _seo_error(exc) from exc


@router.post("/seo/notify")
async def seo_notify(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    """Tell search engines the live site changed (SEO plan)."""
    site = await website.get_or_create(session, location)
    try:
        seo.require(site)
    except PermissionError as exc:
        raise _seo_error(exc) from exc
    if not seo.listed(site):
        raise HTTPException(409, "Publish your site (and allow search engines) first")
    return await seo.notify_search_engines(session, site, website.public_url(site))


# --- Public site ------------------------------------------------------------------------------------------------


def _html(page: str | None, status: int = 200) -> HTMLResponse:
    if page is None:
        return HTMLResponse(site_render.not_found_page(), status_code=404)
    # Short cache: menu, prices and hours are read live, so changes show within a minute.
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "public, max-age=60"})


@public.get("/sites/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def public_site(slug: str, session: AsyncSession = Depends(get_session)):
    site = await session.scalar(select(Website).where(Website.slug == slug.lower()))
    if site is None:
        return _html(None)
    return _html(await website.render_page(session, site, preview=False))


async def _menu_page(site: Website | None, session: AsyncSession) -> Response:
    """The live menu page. If the owner has turned it off, old links go to the home page instead of a dead end."""
    if site is None:
        return _html(None)
    page = await website.render_page(session, site, preview=False, page="menu")
    if page is None and site.published:
        return RedirectResponse(website.public_url(site), status_code=302)
    return _html(page)


@public.get("/sites/{slug}/menu", response_class=HTMLResponse, include_in_schema=False)
async def public_menu(slug: str, session: AsyncSession = Depends(get_session)):
    site = await session.scalar(select(Website).where(Website.slug == slug.lower()))
    return await _menu_page(site, session)


@public.get("/sites/{slug}/assets/{asset_id}", include_in_schema=False)
async def public_asset(slug: str, asset_id: str, session: AsyncSession = Depends(get_session)):
    site = await session.scalar(select(Website).where(Website.slug == slug.lower()))
    asset = await session.get(SiteAsset, asset_id) if site else None
    if asset is None or asset.location_id != site.location_id:
        raise HTTPException(404, "Not found")
    data = (await session.execute(select(SiteAsset.data).where(SiteAsset.id == asset.id))).scalar_one()
    return Response(data, media_type=asset.content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _xml(body: str) -> Response:
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


def _assets(site: Website) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/sites/{site.slug}/assets"


async def _site_for_host(request: Request, session: AsyncSession) -> Website | None:
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return await session.scalar(select(Website).where(Website.custom_domain == host)) if host else None


@public.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots(request: Request, session: AsyncSession = Depends(get_session)):
    """On a restaurant's own domain: everything is crawlable. On this app's address: only the websites."""
    site = await _site_for_host(request, session)
    if site is not None:
        return PlainTextResponse(site_seo.robots(f"https://{site.custom_domain}/sitemap.xml", only_sites=False))
    base = get_settings().app_base_url.rstrip("/")
    return PlainTextResponse(site_seo.robots(f"{base}/sitemap.xml", only_sites=True))


@public.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request, session: AsyncSession = Depends(get_session)):
    site = await _site_for_host(request, session)
    if site is not None:
        entries = seo.sitemap_entries(site, website.public_url(site), _assets(site)) if seo.listed(site) else []
        return _xml(site_seo.sitemap(entries))
    # This app's own address lists every published site that doesn't have its own domain.
    rows = await session.scalars(select(Website).where(Website.published.is_not(None),
                                                       Website.custom_domain.is_(None)).limit(45000))
    return _xml(site_seo.sitemap([en for s in rows if seo.listed(s)
                                  for en in seo.sitemap_entries(s, website.public_url(s), _assets(s))]))


@public.get("/sites/{slug}/sitemap.xml", include_in_schema=False)
async def site_sitemap(slug: str, session: AsyncSession = Depends(get_session)):
    site = await session.scalar(select(Website).where(Website.slug == slug.lower()))
    if site is None:
        raise HTTPException(404, "Not found")
    entries = seo.sitemap_entries(site, website.public_url(site), _assets(site)) if seo.listed(site) else []
    return _xml(site_seo.sitemap(entries))


@public.get("/menu", response_class=HTMLResponse, include_in_schema=False)
async def custom_domain_menu(request: Request, session: AsyncSession = Depends(get_session)):
    """The menu page on a restaurant's own domain (e.g. www.demobistro.ca/menu)."""
    return await _menu_page(await _site_for_host(request, session), session)


@public.get("/{key}.txt", response_class=PlainTextResponse, include_in_schema=False)
async def indexnow_key(key: str):
    """IndexNow asks for this file to prove the change notices come from the site's owner."""
    if key != seo.indexnow_key():
        raise HTTPException(404, "Not found")
    return PlainTextResponse(key)


@public.get("/", response_class=HTMLResponse, include_in_schema=False)
async def custom_domain_home(request: Request, session: AsyncSession = Depends(get_session)):
    """A restaurant's own domain (e.g. www.demobistro.ca) pointed at this server shows its published site."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    site = await session.scalar(select(Website).where(Website.custom_domain == host)) if host else None
    if site is None:
        if request.query_params.get("merchant_id") or request.query_params.get("client_id"):
            # Clover sends merchants to the Site URL itself in some flows: treat it as a launch.
            from app.api.integrations import clover_launch_redirect

            return RedirectResponse(clover_launch_redirect(request.query_params.get("merchant_id")))
        return HTMLResponse(site_render.not_found_page("Nothing here."), status_code=404)
    return _html(await website.render_page(session, site, preview=False))
