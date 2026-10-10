"""The agent loop: stream Claude's answer, run tool calls against our database, log usage and cost.

History kept for follow-up questions is text only (questions and final answers, not tool results),
which keeps later turns small and cheap.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLM, LLMResult
from app.agent.pricing import Usage, cost_usd
from app.agent.prompts import AGENT_SYSTEM, context_block
from app.agent.router import choose_model
from app.agent.tools import TOOLS, ToolContext, ToolError, execute_tool
from app.config import get_settings
from app.models import AIUsage, Conversation, Integration, Location, Message, utcnow

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 12
TOOL_RESULT_MAX_CHARS = 12_000


async def month_spend(session: AsyncSession, location_id: str) -> float:
    now = utcnow()
    start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
    total = await session.scalar(
        select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
            AIUsage.location_id == location_id, AIUsage.created_at >= start
        )
    )
    return float(total or 0.0)


async def record_usage(session: AsyncSession, location_id: str, purpose: str, model: str, usage: Usage) -> float:
    cost = cost_usd(model, usage)
    session.add(
        AIUsage(
            location_id=location_id, purpose=purpose, model=model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens, cost_usd=cost,
        )
    )
    return cost


async def _system(session: AsyncSession, location: Location) -> list[dict]:
    providers = list(
        await session.scalars(
            select(Integration.provider).where(Integration.location_id == location.id, Integration.status == "active")
        )
    )
    return [
        {"type": "text", "text": AGENT_SYSTEM, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": context_block(name=location.name, tz=location.timezone,
                                               currency=location.currency, integrations=providers)},
    ]


async def _history(session: AsyncSession, conversation_id: str) -> list[dict]:
    rows = list(
        await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc()).limit(HISTORY_MESSAGES)
        )
    )
    msgs = []
    for m in reversed(rows):
        text = "".join(b.get("text", "") for b in m.content if b.get("type") == "text").strip()
        if text:
            msgs.append({"role": m.role, "content": text})
    # The API expects the conversation to start with a user turn and alternate.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    cleaned: list[dict] = []
    for m in msgs:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1]["content"] += "\n\n" + m["content"]
        else:
            cleaned.append(m)
    if cleaned and cleaned[-1]["role"] == "user":  # an unanswered question (e.g. earlier error)
        cleaned.pop()
    return cleaned


async def run_chat(
    session: AsyncSession,
    llm: LLM,
    *,
    location: Location,
    user_id: str,
    conversation: Conversation,
    text: str,
    deep: bool = False,
) -> AsyncIterator[dict]:
    """Yields events: meta, text (delta), tool, action, done, error."""
    settings = get_settings()
    # Plain values: ORM objects expire if we have to roll back after an error.
    conversation_id, location_id = conversation.id, location.id
    history = await _history(session, conversation_id)
    session.add(Message(conversation_id=conversation.id, role="user", content=[{"type": "text", "text": text}]))
    if not conversation.title:
        conversation.title = text[:80]
    await session.commit()

    budget_exceeded = await month_spend(session, location.id) >= settings.monthly_ai_budget_usd
    model = choose_model(
        text, deep_requested=deep, budget_exceeded=budget_exceeded,
        default_model=settings.model_default, deep_model=settings.model_deep,
    )
    yield {"type": "meta", "conversation_id": conversation.id, "model": model, "budget_limited": budget_exceeded}

    system = await _system(session, location)
    messages = history + [{"role": "user", "content": text}]
    ctx = ToolContext(session=session, location=location, user_id=user_id)
    total = Usage()
    answer_parts: list[str] = []
    tz = ZoneInfo(location.timezone)

    try:
        for round_no in range(settings.agent_max_tool_rounds + 1):
            last_round = round_no == settings.agent_max_tool_rounds
            result: LLMResult | None = None
            started_text = False
            async for kind, value in llm.stream(
                model=model, system=system, tools=TOOLS, messages=messages,
                max_tokens=settings.agent_max_output_tokens,
                # Last round: answer with what we have instead of calling more tools.
                tool_choice={"type": "none"} if last_round else None,
            ):
                if kind == "text":
                    if not started_text and answer_parts:
                        yield {"type": "text", "delta": "\n\n"}  # keep each round's text a separate paragraph
                    started_text = True
                    yield {"type": "text", "delta": value}
                else:
                    result = value
            assert result is not None
            total = total.add(result.usage)
            if result.text:
                answer_parts.append(result.text)
            messages.append({"role": "assistant", "content": result.content})
            if result.stop_reason != "tool_use" or not result.tool_uses:
                break
            tool_results = []
            for block in result.tool_uses:
                yield {"type": "tool", "name": block["name"]}
                try:
                    output = await execute_tool(block["name"], block.get("input") or {}, ctx)
                    is_error = False
                except ToolError as exc:
                    output, is_error = {"error": str(exc)}, True
                if isinstance(output, dict) and "proposal" in output:
                    yield {"type": "action", "proposal": output["proposal"]}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(output, default=str)[:TOOL_RESULT_MAX_CHARS],
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
    except Exception:  # noqa: BLE001 - surface a friendly error, keep the usage we already paid for
        log.exception("agent failed for location %s", location_id)
        await session.rollback()
        message = "Sorry, something went wrong answering that. Please try again."
        yield {"type": "error", "message": message}
        answer_parts.append(message)

    answer = "\n\n".join(p.strip() for p in answer_parts if p.strip())
    session.add(Message(conversation_id=conversation_id, role="assistant", content=[{"type": "text", "text": answer}]))
    cost = await record_usage(session, location_id, "chat", model, total)
    conv = await session.get(Conversation, conversation_id)
    if conv is not None:
        conv.updated_at = datetime.now(tz)
    await session.commit()
    yield {
        "type": "done",
        "model": model,
        "usage": total.__dict__,
        "cost_usd": cost,
        "proposals": ctx.proposals,
    }


async def answer_text(session: AsyncSession, llm: LLM, **kwargs) -> tuple[str, list[dict]]:
    """Non-streaming helper (WhatsApp/SMS): returns the final answer and any proposals."""
    parts: list[str] = []
    proposals: list[dict] = []
    async for event in run_chat(session, llm, **kwargs):
        if event["type"] == "text":
            parts.append(event["delta"])
        elif event["type"] == "error":
            parts = [event["message"]]
        elif event["type"] == "done":
            proposals = event["proposals"]
    return "".join(parts).strip(), proposals
