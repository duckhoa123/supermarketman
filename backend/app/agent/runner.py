"""The agent loop, shared by all three agents: stream Claude's answer, run tool calls against our database,
log usage and cost.

Which agent runs (Restaurant Manager, Customer agent, Driver Assistant) decides the system prompt, the tools
and whose budget pays: the restaurant's for the Restaurant Manager, the person's own for the other two.

History kept for follow-up questions is text only (questions and final answers, not tool results),
which keeps later turns small and cheap.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools_customer, tools_driver
from app.agent.llm import LLM, LLMResult
from app.agent.pricing import Usage, cost_usd
from app.agent.profiles import get_profile, persona_block
from app.agent.prompts import AGENT_SYSTEM, CUSTOMER_SYSTEM, DRIVER_SYSTEM, context_block, personal_context
from app.agent.router import choose_model
from app.agent.tools import TOOLS, ToolContext, ToolError, execute_tool
from app.config import get_settings
from app.models import AIUsage, Conversation, Integration, Message, utcnow

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 12
TOOL_RESULT_MAX_CHARS = 12_000


@dataclass(frozen=True)
class AgentSpec:
    system: str
    tools: list[dict]
    execute: Callable[[str, dict, ToolContext], Awaitable[dict]]


AGENT_SPECS = {
    "restaurant_manager": AgentSpec(AGENT_SYSTEM, TOOLS, execute_tool),
    "customer": AgentSpec(CUSTOMER_SYSTEM, tools_customer.CUSTOMER_TOOLS, tools_customer.execute),
    "driver": AgentSpec(DRIVER_SYSTEM, tools_driver.DRIVER_TOOLS, tools_driver.execute),
}


def _month_start() -> datetime:
    now = utcnow()
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


async def month_spend(session: AsyncSession, location_id: str) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
            AIUsage.location_id == location_id, AIUsage.created_at >= _month_start()
        )
    )
    return float(total or 0.0)


async def personal_month_spend(session: AsyncSession, user_id: str) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(
            AIUsage.user_id == user_id, AIUsage.location_id.is_(None), AIUsage.created_at >= _month_start()
        )
    )
    return float(total or 0.0)


async def record_usage(session: AsyncSession, location_id: str | None, purpose: str, model: str, usage: Usage,
                       *, user_id: str | None = None, agent: str | None = None) -> float:
    cost = cost_usd(model, usage)
    session.add(
        AIUsage(
            location_id=location_id, user_id=user_id, agent=agent, purpose=purpose, model=model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens, cost_usd=cost,
        )
    )
    return cost


async def _system(session: AsyncSession, spec: AgentSpec, ctx: ToolContext) -> list[dict]:
    persona = persona_block(await get_profile(session, ctx.user_id, ctx.agent))
    if ctx.agent == "restaurant_manager":
        loc = ctx.location
        providers = list(await session.scalars(
            select(Integration.provider).where(Integration.location_id == loc.id, Integration.status == "active")))
        dynamic = context_block(name=loc.name, tz=loc.timezone, currency=loc.currency, integrations=providers,
                                persona=persona)
    elif ctx.agent == "driver":
        d = ctx.driver
        dynamic = personal_context(persona=persona, extra=f"Driver: {d.name}, vehicle {d.vehicle.replace('_', '-')}, "
                                   f"status {d.status.replace('_', ' ')}.")
    else:
        extra = "The customer shared their current location." if ctx.position else ""
        dynamic = personal_context(persona=persona, extra=extra)
    return [{"type": "text", "text": spec.system, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic}]


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
    conversation: Conversation,
    text: str,
    user_id: str,
    location=None,
    agent: str = "restaurant_manager",
    driver=None,
    position: tuple[float, float] | None = None,
    deep: bool = False,
) -> AsyncIterator[dict]:
    """Yields events: meta, text (delta), tool, action, done, error."""
    settings = get_settings()
    spec = AGENT_SPECS[agent]
    # Plain values: ORM objects expire if we have to roll back after an error.
    conversation_id = conversation.id
    location_id = location.id if location is not None else None
    history = await _history(session, conversation_id)
    session.add(Message(conversation_id=conversation.id, role="user", content=[{"type": "text", "text": text}]))
    if not conversation.title:
        conversation.title = text[:80]
    await session.commit()

    if location_id:
        budget_exceeded = await month_spend(session, location_id) >= settings.monthly_ai_budget_usd
    else:
        budget_exceeded = await personal_month_spend(session, user_id) >= settings.personal_monthly_ai_budget_usd
    model = choose_model(
        text, deep_requested=deep, budget_exceeded=budget_exceeded,
        default_model=settings.model_default, deep_model=settings.model_deep,
    )
    yield {"type": "meta", "conversation_id": conversation_id, "model": model, "budget_limited": budget_exceeded,
           "agent": agent}

    ctx = ToolContext(session=session, user_id=user_id, location=location, agent=agent, driver=driver,
                      position=position)
    system = await _system(session, spec, ctx)
    messages = history + [{"role": "user", "content": text}]
    total = Usage()
    answer_parts: list[str] = []

    try:
        for round_no in range(settings.agent_max_tool_rounds + 1):
            last_round = round_no == settings.agent_max_tool_rounds
            result: LLMResult | None = None
            started_text = False
            async for kind, value in llm.stream(
                model=model, system=system, tools=spec.tools, messages=messages,
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
                before = len(ctx.proposals)
                try:
                    output = await spec.execute(block["name"], block.get("input") or {}, ctx)
                    is_error = False
                except ToolError as exc:
                    output, is_error = {"error": str(exc)}, True
                for proposal in ctx.proposals[before:]:
                    yield {"type": "action", "proposal": proposal}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(output, default=str)[:TOOL_RESULT_MAX_CHARS],
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
    except Exception:  # noqa: BLE001 - surface a friendly error, keep the usage we already paid for
        log.exception("agent %s failed (location %s, user %s)", agent, location_id, user_id)
        await session.rollback()
        message = "Sorry, something went wrong answering that. Please try again."
        yield {"type": "error", "message": message}
        answer_parts.append(message)

    answer = "\n\n".join(p.strip() for p in answer_parts if p.strip())
    session.add(Message(conversation_id=conversation_id, role="assistant", content=[{"type": "text", "text": answer}]))
    cost = await record_usage(session, location_id, "chat", model, total,
                              user_id=None if location_id else user_id, agent=agent)
    conv = await session.get(Conversation, conversation_id)
    if conv is not None:
        conv.updated_at = utcnow()
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
