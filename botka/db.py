"""Database models and connection management."""

import json
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class TgUser(Base):
    """Telegram user information."""

    __tablename__ = "tg_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class TgChat(Base):
    """Telegram chat information."""

    __tablename__ = "tg_chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class TgUserInChat(Base):
    """Track users in chats."""

    __tablename__ = "tg_users_in_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_member: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TgChatTopic(Base):
    """Telegram chat topic information."""

    __tablename__ = "tg_chat_topics"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    closed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    icon_color: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    icon_emoji: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    id_name: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    id_icon_emoji: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Resident(Base):
    """Resident tracking."""

    __tablename__ = "residents"

    rowid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    begin_date: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UserMac(Base):
    """User MAC addresses for presence detection."""

    __tablename__ = "user_macs"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mac: Mapped[str] = mapped_column(String, primary_key=True)


class UserSshKey(Base):
    """User SSH public keys."""

    __tablename__ = "user_ssh_keys"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)


class NeededItem(Base):
    """Shopping list items."""

    __tablename__ = "needed_items"

    rowid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pinned_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pinned_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    item: Mapped[str] = mapped_column(Text, nullable=False)


class BorrowedItem(Base):
    """Borrowed items tracking."""

    __tablename__ = "borrowed_items"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bot_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    items: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class BorrowedItemReminder(Base):
    """Borrowed items reminders tracking."""

    __tablename__ = "borrowed_items_reminders"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_name: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reminder_sent: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class TrackedPoll(Base):
    """Tracked polls."""

    __tablename__ = "tracked_polls"

    tg_poll_id: Mapped[str] = mapped_column(String, primary_key=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    info_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    info_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    voted_users: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array


class DashboardMessage(Base):
    """Dashboard messages."""

    __tablename__ = "dashboard_messages"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class ChatHistory(Base):
    """Chat history for NLP."""

    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    classification_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    used_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Memory(Base):
    """NLP memories."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expiration_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class TempOpenToken(Base):
    """Temporary door open tokens for guests."""

    __tablename__ = "temp_open_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    resident_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guest_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ConfigOption(Base):
    """Key-value configuration storage."""

    __tablename__ = "options"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# Database engine management
_engine = None
_async_engine = None
_session_factory = None


def get_db_path() -> str:
    """Get database path from environment or default."""
    return os.environ.get("DB_PATH", "db.sqlite3")


def get_engine():
    """Get synchronous SQLAlchemy engine."""
    global _engine
    if _engine is None:
        db_path = get_db_path()
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    return _engine


def get_async_engine():
    """Get async SQLAlchemy engine."""
    global _async_engine
    if _async_engine is None:
        db_path = get_db_path()
        _async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", echo=False
        )
    return _async_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_async_engine(), expire_on_commit=False
        )
    return _session_factory


async def init_db():
    """Initialize database tables."""
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Get a new async database session."""
    factory = get_session_factory()
    return factory()
