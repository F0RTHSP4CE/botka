from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from botka.config import Settings
from botka.db.models import Base


@pytest.fixture()
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
async def session(engine) -> AsyncSession:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        shopping_topic_id=42,
        bootstrap_resident_ids=[1001],
    )
