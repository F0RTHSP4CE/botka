from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from aiogram import Bot
from aiogram.types import (
    InputRichBlockParagraph,
    InputRichMessage,
    RichTextMention,
    RichTextUrl,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import User, VisitEvent, VisitTracking
from botka.services.telegram_retry import call_with_retry_after

logger = logging.getLogger(__name__)

type VisitAction = Literal[
    "help",
    "plan",
    "cancel",
    "track",
    "untrack",
    "trackers",
    "tracking",
]
type RichUserIdentity = RichTextMention | RichTextUrl


@dataclass(slots=True)
class DeliveryReport:
    notified: list[User]
    failed: list[User]


class VisitService:
    """Persist visit subscriptions and command-usage events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def track(self, tracker_user_id: int, tracked_user_id: int) -> bool:
        """Create a tracking relationship, returning whether it was newly added."""
        existing = await self._session.scalar(
            select(VisitTracking).where(
                VisitTracking.tracker_user_id == tracker_user_id,
                VisitTracking.tracked_user_id == tracked_user_id,
            )
        )
        if existing is not None:
            return False
        self._session.add(
            VisitTracking(
                tracker_user_id=tracker_user_id,
                tracked_user_id=tracked_user_id,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            # A concurrent identical request can win after the lookup above.
            await self._session.rollback()
            return False
        return True

    async def untrack(self, tracker_user_id: int, tracked_user_id: int) -> bool:
        """Remove a tracking relationship, returning whether one existed."""
        tracking = await self._session.scalar(
            select(VisitTracking).where(
                VisitTracking.tracker_user_id == tracker_user_id,
                VisitTracking.tracked_user_id == tracked_user_id,
            )
        )
        if tracking is None:
            return False
        await self._session.delete(tracking)
        await self._session.commit()
        return True

    async def list_trackers(self, tracked_user_id: int) -> Sequence[User]:
        """Return users subscribed to one user's visit notifications."""
        result = await self._session.execute(
            select(User)
            .join(
                VisitTracking,
                User.id == VisitTracking.tracker_user_id,
            )
            .where(VisitTracking.tracked_user_id == tracked_user_id)
            .order_by(*_user_order())
        )
        return result.scalars().all()

    async def list_tracking(self, tracker_user_id: int) -> Sequence[User]:
        """Return users whose visits the given user tracks."""
        result = await self._session.execute(
            select(User)
            .join(
                VisitTracking,
                User.id == VisitTracking.tracked_user_id,
            )
            .where(VisitTracking.tracker_user_id == tracker_user_id)
            .order_by(*_user_order())
        )
        return result.scalars().all()

    async def list_trackers_for_users(
        self, tracked_user_ids: Iterable[int]
    ) -> dict[int, list[User]]:
        """Batch tracker lookups for MAC arrivals, grouped by tracked user ID."""
        user_ids = list(tracked_user_ids)
        if not user_ids:
            return {}
        result = await self._session.execute(
            select(VisitTracking.tracked_user_id, User)
            .join(User, User.id == VisitTracking.tracker_user_id)
            .where(VisitTracking.tracked_user_id.in_(user_ids))
            .order_by(VisitTracking.tracked_user_id, *_user_order())
        )
        trackers: defaultdict[int, list[User]] = defaultdict(list)
        for tracked_user_id, tracker in result.all():
            trackers[tracked_user_id].append(tracker)
        return dict(trackers)

    async def record_event(self, user_id: int, action: VisitAction) -> None:
        """Append one privacy-minimal `/visit` usage event."""
        self._session.add(VisitEvent(user_id=user_id, action=action))
        await self._session.commit()


def rich_user_identity(user: User) -> RichUserIdentity:
    """Build a rich handle mention, falling back to a linked numeric ID."""
    if user.username:
        username = user.username.removeprefix("@")
        return RichTextMention(text=f"@{username}", username=username)
    return RichTextUrl(
        text=str(user.telegram_id),
        url=f"tg://user?id={user.telegram_id}",
    )


def build_plan_notification(user: User, description: str) -> InputRichMessage:
    """Build a plan notification with the free-form description as plain text."""
    return InputRichMessage(
        blocks=[
            InputRichBlockParagraph(
                text=[rich_user_identity(user), " plans to visit F0:"]
            ),
            # Structured rich-text strings are plain text. User input is not parsed
            # as HTML or Markdown.
            InputRichBlockParagraph(text=description),
        ],
        skip_entity_detection=True,
    )


def build_cancel_notification(user: User) -> InputRichMessage:
    """Build a visit-cancellation notification for one planner."""
    return InputRichMessage(
        blocks=[
            InputRichBlockParagraph(
                text=[rich_user_identity(user), " canceled their visit."]
            )
        ],
        skip_entity_detection=True,
    )


def build_arrival_notification(user: User) -> InputRichMessage:
    """Build the notification emitted when MAC tracking detects an arrival."""
    return InputRichMessage(
        blocks=[
            InputRichBlockParagraph(
                text=[rich_user_identity(user), " has arrived at F0."]
            )
        ],
        skip_entity_detection=True,
    )


async def deliver_rich_message(
    bot: Bot,
    recipients: Sequence[User],
    rich_message: InputRichMessage,
    *,
    description: str,
) -> DeliveryReport:
    """Deliver independently to each recipient and return both result groups."""
    report = DeliveryReport(notified=[], failed=[])
    for recipient in recipients:
        try:
            await call_with_retry_after(
                lambda recipient=recipient: bot.send_rich_message(
                    chat_id=recipient.telegram_id,
                    rich_message=rich_message,
                ),
                description=f"{description} to {recipient.telegram_id}",
            )
        except Exception:
            logger.exception(
                "Failed to send %s to Telegram user %s",
                description,
                recipient.telegram_id,
            )
            report.failed.append(recipient)
        else:
            report.notified.append(recipient)
    return report


def _user_order():
    return (
        User.username.is_(None),
        func.lower(User.username),
        User.telegram_id,
    )
