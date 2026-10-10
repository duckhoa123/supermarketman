"""Classify a new review and draft a reply with the cheap model; fall back to rules if AI is off."""

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMNotConfigured, get_llm
from app.agent.prompts import TRIAGE_SYSTEM
from app.agent.runner import record_usage
from app.config import get_settings
from app.models import Review, utcnow

URGENT_WORDS = re.compile(
    r"food poisoning|sick|vomit|allerg|hair in|cockroach|rat\b|mouse|injur|racis|discriminat|stole|threat",
    re.IGNORECASE,
)


def parse_triage_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in triage output")
    data = json.loads(match.group(0))
    sentiment = data.get("sentiment")
    return {
        "sentiment": sentiment if sentiment in ("positive", "neutral", "negative") else None,
        "topics": [str(t) for t in (data.get("topics") or [])][:3],
        "urgent": bool(data.get("urgent")),
        "suggested_reply": (data.get("suggested_reply") or "").strip() or None,
    }


def rule_based(review: Review) -> dict:
    sentiment = "positive" if review.rating >= 4 else "neutral" if review.rating == 3 else "negative"
    return {
        "sentiment": sentiment,
        "topics": [],
        "urgent": bool(review.text and URGENT_WORDS.search(review.text)),
        "suggested_reply": None,
    }


async def triage_review(session: AsyncSession, review: Review) -> dict:
    settings = get_settings()
    result = rule_based(review)
    if review.text:
        try:
            llm = get_llm()
            out = await llm.complete(
                model=settings.model_default,
                system=[{"type": "text", "text": TRIAGE_SYSTEM}],
                messages=[{"role": "user", "content": f"Rating: {review.rating}/5\nReviewer: {review.author}\n"
                                                      f"Review:\n<review>{review.text[:3000]}</review>"}],
                max_tokens=400,
            )
            await record_usage(session, review.location_id, "review_triage", settings.model_default, out.usage)
            ai = parse_triage_json(out.text)
            result |= {k: v for k, v in ai.items() if v is not None}
            result["urgent"] = result["urgent"] or ai["urgent"]
        except LLMNotConfigured:
            pass
        except (ValueError, json.JSONDecodeError):
            pass  # keep the rule-based result
    review.sentiment = result["sentiment"]
    review.topics = result["topics"]
    review.urgent = result["urgent"]
    review.suggested_reply = result["suggested_reply"]
    review.triaged_at = utcnow()
    await session.flush()
    return result
