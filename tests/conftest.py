"""Pytest configuration and fixtures."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from botka.db import Base
from botka.config import (
    Config,
    TelegramConfig,
    TelegramChats,
    ServicesConfig,
    MikrotikConfig,
    ButlerConfig,
    NlpConfig,
    ThreadIdPair,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_engine():
    """Create in-memory SQLite database for tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Create database session for tests."""
    async_session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.fixture
def mock_config():
    """Create mock configuration."""
    return Config(
        telegram=TelegramConfig(
            token="test_token_123",
            admins=[123456789],
            passive_mode=False,
            chats=TelegramChats(
                residential=[-1001234567890],
                needs=ThreadIdPair(chat=-1001234567890, thread=123),
                mac_monitoring=ThreadIdPair(chat=-1001234567890, thread=456),
            ),
        ),
        server_addr="0.0.0.0:8080",
        services=ServicesConfig(
            mikrotik=MikrotikConfig(
                host="10.0.0.1",
                username="api",
                password="secret",
                scheme="http",
            ),
            butler=ButlerConfig(
                url="http://butler.local/control",
                token="butler_secret",
            ),
        ),
        nlp=NlpConfig(
            enabled=True,
            trigger_words=["bot", "botka"],
            models=["gpt-4o-mini"],
            max_history=100,
        ),
    )


@pytest.fixture
def mock_bot():
    """Create mock Telegram bot."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.forward_message = AsyncMock()
    return bot


@pytest.fixture
def mock_update():
    """Create mock Telegram update."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.chat_id = -1001234567890
    update.message.message_id = 1
    update.message.message_thread_id = None
    update.message.text = "test message"
    update.message.from_user = MagicMock()
    update.message.from_user.id = 123456789
    update.message.from_user.username = "testuser"
    update.message.from_user.first_name = "Test"
    update.message.from_user.last_name = "User"
    update.message.from_user.is_bot = False
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.message.chat.type = "supergroup"
    update.effective_user = update.message.from_user
    return update


@pytest.fixture
def mock_context(mock_bot):
    """Create mock context."""
    context = MagicMock()
    context.bot = mock_bot
    context.args = []
    return context


@pytest.fixture
def mock_http_client():
    """Create mock HTTP client."""
    client = AsyncMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    return client
