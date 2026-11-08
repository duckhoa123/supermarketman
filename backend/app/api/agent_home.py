"""Behind each agent's home: what it's working on, its activity record, what it remembers, what it can reach.

The Restaurant Manager's live under /locations/{id}/... (shared by the restaurant's team, memory per person);
the Customer agent's and Driver Assistant's under /agents/{agent}/... (one person's own).
"""

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import memory
from app.agent.runner import month_spend, personal_month_spend
from app.api.agents import _personal_agent
from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.models import AgentMemory, Integration, Location
from app.services import agent_work
from app.tenancy import require_location

router = APIRouter(tags=["agent home"])

# What each connection lets the Restaurant Manager do. Anything that leaves Pass always waits for a person.
REACH = {
    "clover": ("Clover POS", "Reads sales, orders, stock and staff. Sends app orders and menu changes you approve."),
    "square": ("Square POS", "Reads sales, orders, stock and staff."),
    "google": ("Google reviews", "Reads reviews. Posts a reply only when you tap Post."),
}


def _memory_out(m: AgentMemory) -> dict:
    return {"id": m.id, "text": m.text, "source": m.source, "created_at": m.created_at.isoformat()}


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=memory.MAX_CHARS)


# --- Restaurant Manager ---------------------------------------------------------------------------------------


@router.get("/locations/{location_id}/work")
async def restaurant_work(location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    return await agent_work.restaurant_work(session, location, user.id)


@router.get("/locations/{location_id}/memory")
async def restaurant_memory(location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
                            session: AsyncSession = Depends(get_session)):
    rows = await memory.list_memories(session, user.id, "restaurant_manager", location.id)
    return {"memories": [_memory_out(m) for m in rows]}


@router.post("/locations/{location_id}/memory")
async def add_restaurant_memory(body: MemoryIn, location: Location = Depends(require_location),
                                user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    m = await memory.add_memory(session, user.id, "restaurant_manager", location.id, body.text, source="app")
    await session.commit()
    return _memory_out(m)


@router.delete("/locations/{location_id}/memory/{memory_id}")
async def forget_restaurant_memory(memory_id: str, location: Location = Depends(require_location),
                                   user: AuthUser = Depends(current_user),
                                   session: AsyncSession = Depends(get_session)):
    m = await session.get(AgentMemory, memory_id)
    if m is None or m.user_id != user.id or m.location_id != location.id or m.agent != "restaurant_manager":
        raise HTTPException(404, "Not found")
    await session.delete(m)
    await session.commit()
    return {"ok": True}


@router.get("/locations/{location_id}/agent-access")
async def restaurant_access(location: Location = Depends(require_location),
                            session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Integration).where(Integration.location_id == location.id))
    by_provider = {r.provider: r for r in rows}
    connections = []
    for provider, (name, can) in REACH.items():
        r = by_provider.get(provider)
        if provider == "square" and r is None:
            continue  # not offered yet
        status = r.status if r else "disconnected"
        connections.append({
            "provider": provider, "name": name, "can": can, "status": status,
            "last_synced_at": r.last_synced_at.isoformat() if r and r.last_synced_at else None,
            "last_error": r.last_error if r and status == "error" else None,
        })
    spent = await month_spend(session, location.id)
    budget = get_settings().monthly_ai_budget_usd
    return {"connections": connections, "spend": {"spent_usd": round(spent, 2), "budget_usd": budget,
                                                  "limited": spent >= budget}}


# --- Customer agent and Driver Assistant ------------------------------------------------------------------------


@router.get("/agents/{agent}/work")
async def personal_work(agent: str = Path(...), user: AuthUser = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    driver = await _personal_agent(agent, user, session)
    if agent == "driver":
        return await agent_work.driver_work(session, driver)
    return await agent_work.customer_work(session, user.id)


@router.get("/agents/{agent}/memory")
async def personal_memory(agent: str = Path(...), user: AuthUser = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    rows = await memory.list_memories(session, user.id, agent, None)
    return {"memories": [_memory_out(m) for m in rows]}


@router.post("/agents/{agent}/memory")
async def add_personal_memory(body: MemoryIn, agent: str = Path(...), user: AuthUser = Depends(current_user),
                              session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    m = await memory.add_memory(session, user.id, agent, None, body.text, source="app")
    await session.commit()
    return _memory_out(m)


@router.delete("/agents/{agent}/memory/{memory_id}")
async def forget_personal_memory(memory_id: str, agent: str = Path(...), user: AuthUser = Depends(current_user),
                                 session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    m = await session.get(AgentMemory, memory_id)
    if m is None or m.user_id != user.id or m.agent != agent or m.location_id is not None:
        raise HTTPException(404, "Not found")
    await session.delete(m)
    await session.commit()
    return {"ok": True}


@router.get("/agents/{agent}/access")
async def personal_access(agent: str = Path(...), user: AuthUser = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    settings = get_settings()
    spent = await personal_month_spend(session, user.id)
    budget = settings.personal_monthly_ai_budget_usd
    if agent == "customer":
        connections = [
            {"provider": "payments", "name": "Card payments",
             "can": "Charges your card only when you tap Place order or add a tip.",
             "status": "active" if settings.stripe_secret_key else "test"},
            {"provider": "location", "name": "Your location",
             "can": "Used only when you switch on “Use my current location” in a chat.", "status": "ask"},
        ]
    else:
        connections = [
            {"provider": "jobs", "name": "Dispatch",
             "can": "Reads your jobs, shift and earnings. You mark pickups and drop-offs yourself.",
             "status": "active"},
        ]
    return {"connections": connections, "spend": {"spent_usd": round(spent, 2), "budget_usd": budget,
                                                  "limited": spent >= budget}}
