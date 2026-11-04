"""Restaurant websites: the owner's editor (draft, preview, publish, history, images) and the public site."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import Location, SiteAsset, Website, WebsiteVersion
from app.services import site_render, site_templates, website
from app.tenancy import require_location, require_manager

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
async def preview(edit: bool = False, location: Location = Depends(require_location),
                  session: AsyncSession = Depends(get_session)):
    """The draft as it would look once published (only for the restaurant's team). edit=1 adds the markers the
    visual editor uses (which text and photos can be clicked, each section's id)."""
    site = await website.get_or_create(session, location)
    return HTMLResponse(await website.render_page(session, site, preview=True, edit=edit) or site_render.not_found_page(),
                        headers={"Cache-Control": "no-store"})


@router.post("/publish")
async def publish(location: Location = Depends(require_manager), user: AuthUser = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    site = await website.get_or_create(session, location)
    await website.publish(session, site, user.id)
    return website.site_out(site)


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


@public.get("/sites/{slug}/assets/{asset_id}", include_in_schema=False)
async def public_asset(slug: str, asset_id: str, session: AsyncSession = Depends(get_session)):
    site = await session.scalar(select(Website).where(Website.slug == slug.lower()))
    asset = await session.get(SiteAsset, asset_id) if site else None
    if asset is None or asset.location_id != site.location_id:
        raise HTTPException(404, "Not found")
    data = (await session.execute(select(SiteAsset.data).where(SiteAsset.id == asset.id))).scalar_one()
    return Response(data, media_type=asset.content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})


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
