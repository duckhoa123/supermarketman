"""The Alembic migrations must produce exactly the tables and columns the models declare (Postgres only)."""

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import Base

URL = os.environ["DATABASE_URL"]
pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="migration check runs on Postgres")


async def _reset():
    eng = create_async_engine(URL)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await eng.dispose()


async def _schema() -> dict[str, set[str]]:
    eng = create_async_engine(URL)
    async with eng.connect() as conn:
        def read(sync_conn):
            insp = inspect(sync_conn)
            return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}

        result = await conn.run_sync(read)
    await eng.dispose()
    return result


def test_migrations_match_models():
    from alembic import command
    from alembic.config import Config

    asyncio.run(_reset())
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    command.upgrade(cfg, "head")
    db_schema = asyncio.run(_schema())
    db_schema.pop("alembic_version", None)
    model_schema = {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
    assert db_schema == model_schema
    command.downgrade(cfg, "base")
    assert set(asyncio.run(_schema())) <= {"alembic_version"}
