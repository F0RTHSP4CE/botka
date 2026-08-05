from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import escape as html_escape
from typing import Literal

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import User, UserTier, VisitEvent, VisitTracking
from botka.handlers.user_links import format_user_link
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
            .where(
                VisitTracking.tracked_user_id == tracked_user_id,
                User.tier.in_((UserTier.resident, UserTier.member)),
            )
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
            .where(
                VisitTracking.tracked_user_id.in_(user_ids),
                User.tier.in_((UserTier.resident, UserTier.member)),
            )
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


def _user_link(user: User) -> str:
    return format_user_link(
        telegram_id=user.telegram_id,
        username=user.username,
    )


def build_plan_notification(user: User, description: str) -> str:
    """Build an HTML notification with an escaped free-form description."""
    return f"{_user_link(user)} plans to visit F0: {html_escape(description)}"


def build_cancel_notification(user: User) -> str:
    """Build a visit-cancellation notification for one planner."""
    return f"{_user_link(user)} canceled their visit."


def build_arrival_notification(user: User) -> str:
    """Build the notification emitted when MAC tracking detects an arrival."""
    return f"{_user_link(user)} has arrived at F0."


async def deliver_html_message(
    bot: Bot,
    recipients: Sequence[User],
    text: str,
    *,
    description: str,
) -> DeliveryReport:
    """Deliver independently to each recipient and return both result groups."""
    report = DeliveryReport(notified=[], failed=[])
    for recipient in recipients:
        try:
            await call_with_retry_after(
                lambda recipient=recipient: bot.send_message(
                    chat_id=recipient.telegram_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
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
