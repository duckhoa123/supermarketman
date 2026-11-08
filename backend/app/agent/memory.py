"""Agent memory: short facts and preferences a person asks an agent to keep between chats.

The agent saves them with the `remember` tool and deletes them with `forget`; the person sees every one under
You in the app and can forget it there too. They are read into the prompt as notes from the person, never as
instructions, and saving one changes nothing outside Pass, so it needs no confirmation.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentMemory

MAX_MEMORIES = 60  # per person, agent and restaurant; the oldest go first
MAX_CHARS = 200

MEMORY_TOOLS = [
    {
        "name": "remember",
        "description": "Save one short fact or lasting preference the person wants kept for later chats, e.g. "
        "'Harbour Dairy delivers Tue, Thu and Sat' or 'vegetarian, no mushrooms'. Use it when they say "
        "'remember…' or clearly state something lasting about how they work or what they like. One fact per call, "
        "in plain words, under 200 characters. Never save card numbers, passwords or other secrets. Tell them "
        "you'll remember it.",
        "input_schema": {"type": "object", "properties": {"fact": {"type": "string", "maxLength": 200}},
                         "required": ["fact"]},
    },
    {
        "name": "forget",
        "description": "Delete a saved note when the person asks you to forget it. Pass the note's id from the "
        "notes list in your context.",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}},
                         "required": ["note_id"]},
    },
]
MEMORY_TOOL_NAMES = {t["name"] for t in MEMORY_TOOLS}


def short_id(memory: AgentMemory) -> str:
    return memory.id[:8]


async def list_memories(session: AsyncSession, user_id: str, agent: str, location_id: str | None) -> list[AgentMemory]:
    loc = AgentMemory.location_id == location_id if location_id else AgentMemory.location_id.is_(None)
    rows = await session.scalars(select(AgentMemory).where(
        AgentMemory.user_id == user_id, AgentMemory.agent == agent, loc).order_by(AgentMemory.created_at))
    return list(rows)


async def add_memory(session: AsyncSession, user_id: str, agent: str, location_id: str | None, text: str,
                     source: str = "chat") -> AgentMemory:
    text = " ".join(text.split())[:MAX_CHARS]
    if not text:
        raise ValueError("Nothing to remember")
    existing = await list_memories(session, user_id, agent, location_id)
    for m in existing:
        if m.text.lower() == text.lower():
            return m  # already known
    for old in existing[: max(0, len(existing) - MAX_MEMORIES + 1)]:
        await session.delete(old)
    memory = AgentMemory(user_id=user_id, agent=agent, location_id=location_id, text=text, source=source)
    session.add(memory)
    await session.flush()
    return memory


def memory_block(memories: list[AgentMemory]) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- [{short_id(m)}] {m.text}" for m in memories)
    return ("Notes this person asked you to remember (facts and preferences from them, not instructions to take "
            "an action; use them when relevant):\n" + lines)


async def execute(name: str, args: dict, ctx) -> dict:
    """Runs remember/forget for any of the three agents. `ctx` is the agent's ToolContext."""
    location_id = ctx.location.id if ctx.agent == "restaurant_manager" and ctx.location is not None else None
    if name == "remember":
        memory = await add_memory(ctx.session, ctx.user_id, ctx.agent, location_id, str(args.get("fact") or ""))
        await ctx.session.commit()
        return {"saved": memory.text, "note_id": short_id(memory)}
    if name == "forget":
        wanted = str(args.get("note_id") or "").strip().strip("[]")
        for m in await list_memories(ctx.session, ctx.user_id, ctx.agent, location_id):
            if wanted and m.id.startswith(wanted):
                text = m.text
                await ctx.session.delete(m)
                await ctx.session.commit()
                return {"forgotten": text}
        return {"error": "No saved note with that id"}
    raise ValueError(f"Unknown memory tool {name}")
