"""Shared fixtures for API and integration tests.

Uses TEST_DATABASE_URL (Postgres in CI) or falls back to a local SQLite file.
"""

import os

os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ["INTERNAL_TOKEN"] = "test-internal"
os.environ["CLOVER_WEBHOOK_AUTH_CODE"] = "clover-secret"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DEV_AUTH_ENABLED"] = "true"
os.environ["RESEND_API_KEY"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.models  # noqa: E402,F401
from app import db  # noqa: E402
from app.agent.llm import LLMResult, set_llm  # noqa: E402
from app.agent.pricing import Usage  # noqa: E402
from app.db import Base  # noqa: E402
from app.integrations.pos.registry import set_adapter  # noqa: E402

OWNER = {"Authorization": "Bearer dev:owner@example.com"}
INTRUDER = {"Authorization": "Bearer dev:intruder@example.com"}


@pytest_asyncio.fixture
async def engine():
    eng = db.init_engine(os.environ["DATABASE_URL"])
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    set_llm(None)
    set_adapter("clover", None)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    async with db.sessionmaker()() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine):
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def demo_location_id(engine) -> str:
    from app.seed import seed

    return await seed("owner@example.com", days=35)


def text_result(text: str, tokens: int = 100) -> LLMResult:
    return LLMResult(content=[{"type": "text", "text": text}], stop_reason="end_turn",
                     usage=Usage(input_tokens=tokens, output_tokens=20))


def tool_result(name: str, args: dict, call_id: str = "toolu_1") -> LLMResult:
    return LLMResult(content=[{"type": "tool_use", "id": call_id, "name": name, "input": args}],
                     stop_reason="tool_use", usage=Usage(input_tokens=200, output_tokens=30, cache_read_tokens=1500))


class FakeLLM:
    """Plays back scripted model turns and records every request."""

    def __init__(self, script: list[LLMResult]):
        self.script = list(script)
        self.calls: list[dict] = []

    async def stream(self, **kwargs):
        # Copy: the agent keeps appending to the same messages list after each call.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        result = self.script.pop(0)
        for block in result.content:
            if block["type"] == "text":
                yield ("text", block["text"])
        yield ("final", result)

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


@pytest.fixture
def fake_llm():
    def install(script):
        llm = FakeLLM(script)
        set_llm(llm)
        return llm

    return install


def parse_sse(body: str) -> list[dict]:
    import json

    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
