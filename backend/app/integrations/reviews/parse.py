"""Pure normalization of review payloads (no HTTP)."""

from dataclasses import dataclass
from datetime import datetime, timezone

STAR_RATING = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


@dataclass
class NormalizedReview:
    source: str
    external_id: str
    author: str | None
    rating: int
    text: str | None
    posted_at: datetime
    reply_text: str | None = None
    replied_at: datetime | None = None


def _rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    # Google returns e.g. 2026-10-01T03:04:05.123456Z
    value = value.replace("Z", "+00:00")
    if "." in value:
        head, _, tail = value.partition(".")
        frac, plus, tz = tail.partition("+")
        value = f"{head}.{frac[:6]}{plus}{tz}"
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_google_review(raw: dict) -> NormalizedReview:
    """Google Business Profile v4 `Review` resource."""
    reply = raw.get("reviewReply") or {}
    comment = raw.get("comment")
    # Google appends a machine translation block to translated reviews; keep the original text.
    if comment and "(Original)" in comment:
        comment = comment.split("(Original)", 1)[1].strip()
    return NormalizedReview(
        source="google",
        external_id=raw["name"],  # accounts/{a}/locations/{l}/reviews/{id}; needed to post a reply
        author=(raw.get("reviewer") or {}).get("displayName"),
        rating=STAR_RATING.get(raw.get("starRating", ""), 0),
        text=comment,
        posted_at=_rfc3339(raw.get("createTime")) or datetime.now(timezone.utc),
        reply_text=reply.get("comment"),
        replied_at=_rfc3339(reply.get("updateTime")),
    )
