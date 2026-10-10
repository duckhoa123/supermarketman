"""Tenant isolation: which locations can the signed-in user see?

Every route that touches restaurant data takes a `location_id` and runs it through `require_location`.
Agent tools receive the resolved Location object from the server, never an id chosen by the model.
"""

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import Location, OrgMember


async def user_locations(session: AsyncSession, user_id: str) -> list[Location]:
    rows = await session.scalars(
        select(Location)
        .join(OrgMember, OrgMember.org_id == Location.org_id)
        .where(OrgMember.user_id == user_id)
        .order_by(Location.name)
    )
    return list(rows)


async def user_role(session: AsyncSession, user_id: str, location: Location) -> str | None:
    return await session.scalar(
        select(OrgMember.role).where(OrgMember.user_id == user_id, OrgMember.org_id == location.org_id)
    )


async def require_location(
    location_id: str = Path(...),
    user: AuthUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Location:
    location = await session.get(Location, location_id)
    if location is None or await user_role(session, user.id, location) is None:
        # Same response for "missing" and "not yours" so ids can't be probed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    return location


async def require_manager(
    location: Location = Depends(require_location),
    user: AuthUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Location:
    role = await user_role(session, user.id, location)
    if role not in ("owner", "manager"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner or manager role required")
    return location
