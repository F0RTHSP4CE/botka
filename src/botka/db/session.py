from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, echo=False, future=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        # Snapshot-based anonymous-admin restoration was removed. Drop its
        # legacy table so saved permission state is removed on upgrade too.
        await conn.execute(text("DROP TABLE IF EXISTS anonymous_admin_snapshots"))
        await conn.run_sync(Base.metadata.create_all)
