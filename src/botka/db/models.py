from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserTier(str, enum.Enum):
    resident = "resident"
    member = "member"
    guest = "guest"


class PollAudience(str, enum.Enum):
    residents = "residents"
    members = "members"
    everyone = "everyone"
    all = "all"
    anyone = "anyone"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tier: Mapped[UserTier] = mapped_column(Enum(UserTier), default=UserTier.guest)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AnonymousAdminSnapshot(Base):
    """Administrator state to restore after a chat's anonymous mode ends."""

    __tablename__ = "anonymous_admin_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "telegram_id",
            name="uq_anonymous_admin_snapshot_chat_user",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    was_administrator: Mapped[bool] = mapped_column(Boolean, nullable=False)
    permissions: Mapped[dict[str, bool | None]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_telegram_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bought: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ShoppingNeedsPin(Base):
    __tablename__ = "shopping_needs_pins"
    __table_args__ = (
        UniqueConstraint("chat_id", "topic_id", name="uq_needs_pin_chat_topic"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)


class BorrowedItem(Base):
    __tablename__ = "borrowed_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_telegram_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    returned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    author_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[PollAudience] = mapped_column(Enum(PollAudience), nullable=False)
    awaiting_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (
        UniqueConstraint("poll_id", "user_telegram_id", name="uq_poll_vote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PollOptionVote(Base):
    __tablename__ = "poll_option_votes"
    __table_args__ = (
        UniqueConstraint(
            "poll_id",
            "user_telegram_id",
            "option_id",
            name="uq_poll_option_vote",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    option_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PollIgnoredOption(Base):
    __tablename__ = "poll_ignored_options"
    __table_args__ = (
        UniqueConstraint("poll_id", "option_id", name="uq_poll_ignored_option"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(255), nullable=False)
    option_id: Mapped[int] = mapped_column(Integer, nullable=False)


class PollOption(Base):
    __tablename__ = "poll_options"
    __table_args__ = (UniqueConstraint("poll_id", "option_id", name="uq_poll_option"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(255), nullable=False)
    option_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class MacTrackerDevice(Base):
    __tablename__ = "mac_tracker_devices"
    __table_args__ = (
        UniqueConstraint("user_id", "mac_address", name="uq_mac_tracker_user_mac"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    mac_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))


class PlankaCardMapping(Base):
    __tablename__ = "planka_card_mappings"

    short_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    planka_card_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


class PlankaAttachmentTelegramCache(Base):
    __tablename__ = "planka_attachment_telegram_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    planka_attachment_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlankaTodoMessage(Base):
    __tablename__ = "planka_todo_messages"
    __table_args__ = (
        UniqueConstraint(
            "target_chat_id",
            "topic_id",
            name="uq_planka_todo_message_target_topic",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)


class AgendaTopic(Base):
    __tablename__ = "agenda_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bot_reply_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefinanceInvoiceNotification(Base):
    __tablename__ = "refinance_invoice_notifications"

    invoice_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
