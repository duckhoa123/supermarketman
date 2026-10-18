"""The three agents and their customisable avatars (name, icon, colour, tone)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentProfile

AGENT_LABELS = {
    "restaurant_manager": "Restaurant Manager",
    "customer": "Customer agent",
    "driver": "Driver Assistant",
}
DEFAULTS = {
    "restaurant_manager": {"display_name": "Remy", "avatar": {"icon": "chef", "color": "#2563eb"}, "tone": "concise"},
    "customer": {"display_name": "Coco", "avatar": {"icon": "bowl", "color": "#16a34a"}, "tone": "friendly"},
    "driver": {"display_name": "Dash", "avatar": {"icon": "scooter", "color": "#ea580c"}, "tone": "concise"},
}
ICONS = ("chef", "whisk", "bowl", "fork", "leaf", "star", "scooter", "bike", "car", "map", "robot", "smile")
TONES = {
    "friendly": "warm and encouraging, in plain words",
    "concise": "brief and to the point; numbers first",
    "detailed": "thorough, with the numbers behind each point",
    "playful": "light and upbeat; an occasional emoji is fine",
}


def profile_out(agent: str, p: AgentProfile | None) -> dict:
    d = DEFAULTS[agent]
    return {
        "agent": agent, "label": AGENT_LABELS[agent],
        "display_name": p.display_name if p else d["display_name"],
        "avatar": {**d["avatar"], **(p.avatar or {})} if p else d["avatar"],
        "tone": p.tone if p else d["tone"],
        "customised": p is not None,
    }


async def get_profile(session: AsyncSession, user_id: str, agent: str) -> dict:
    p = await session.get(AgentProfile, (user_id, agent))
    return profile_out(agent, p)


async def save_profile(session: AsyncSession, user_id: str, agent: str, *, display_name: str | None,
                       icon: str | None, color: str | None, tone: str | None) -> dict:
    p = await session.get(AgentProfile, (user_id, agent))
    d = DEFAULTS[agent]
    if p is None:
        p = AgentProfile(user_id=user_id, agent=agent, display_name=d["display_name"], avatar=dict(d["avatar"]),
                         tone=d["tone"])
        session.add(p)
    if display_name is not None:
        p.display_name = display_name.strip()[:60] or d["display_name"]
    avatar = dict(p.avatar or {})
    if icon is not None:
        avatar["icon"] = icon
    if color is not None:
        avatar["color"] = color
    p.avatar = avatar
    if tone is not None:
        p.tone = tone
    await session.commit()
    return profile_out(agent, p)


async def all_profiles(session: AsyncSession, user_id: str) -> dict[str, dict]:
    rows = {p.agent: p for p in await session.scalars(select(AgentProfile).where(AgentProfile.user_id == user_id))}
    return {a: profile_out(a, rows.get(a)) for a in AGENT_LABELS}


def persona_block(profile: dict) -> str:
    return (f"Your name is {profile['display_name']} (the user chose it; use it if asked who you are). "
            f"Tone: {TONES.get(profile['tone'], TONES['friendly'])}.")
