"""Poll review platforms, store new reviews, triage them and raise alerts."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.reviews.google import get_google_client
from app.integrations.reviews.parse import NormalizedReview
from app.models import Integration, Location, Review
from app.services.integration_store import access_token
from app.services.jobs import enqueue


async def store_reviews(session: AsyncSession, location: Location, reviews: list[NormalizedReview]) -> list[Review]:
    """Upsert; returns the reviews that are new to us."""
    if not reviews:
        return []
    existing = {
        r.external_id: r
        for r in await session.scalars(
            select(Review).where(
                Review.location_id == location.id,
                Review.external_id.in_([n.external_id for n in reviews]),
            )
        )
    }
    new: list[Review] = []
    for n in reviews:
        row = existing.get(n.external_id)
        if row is None:
            row = Review(location_id=location.id, source=n.source, external_id=n.external_id, topics=[])
            session.add(row)
            new.append(row)
        row.author = n.author
        row.rating = n.rating
        row.text = n.text
        row.posted_at = n.posted_at
        row.reply_text = n.reply_text
        row.replied_at = n.replied_at
    await session.flush()
    return new


async def poll_google(session: AsyncSession, integ: Integration) -> dict:
    location = await session.get(Location, integ.location_id)
    if not integ.external_id:
        return {"skipped": "no Google location selected"}
    token = await access_token(session, integ)
    reviews = await get_google_client().list_reviews(token, integ.external_id)
    first_poll = not (integ.cursor or {}).get("polled")
    new = await store_reviews(session, location, reviews)
    for review in new:
        # On the first poll we import history quietly: triage without alerting on old reviews.
        await enqueue(session, "triage_review", {"review_id": review.id, "alert": not first_poll},
                      dedupe_key=f"triage:{review.id}")
    integ.cursor = {**(integ.cursor or {}), "polled": True}
    await session.commit()
    return {"fetched": len(reviews), "new": len(new)}
