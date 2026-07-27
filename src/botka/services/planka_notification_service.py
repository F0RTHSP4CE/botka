from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from aiogram import Bot

from botka.config import Settings
from botka.services.planka_client import PlankaActionEvent
from botka.services.telegram_retry import call_with_retry_after

logger = logging.getLogger(__name__)
CREATE_CARD_ACTION = "createCard"
MOVE_CARD_ACTION = "moveCard"


@dataclass(frozen=True, slots=True)
class PlankaNotificationTarget:
    chat_id: str
    thread_id: int | None


class PlankaNotificationService:
    """Deliver Planka notifications and deduplicate locally announced actions."""

    def __init__(self, settings: Settings) -> None:
        self._targets = tuple(
            PlankaNotificationTarget(chat_id, thread_id)
            for chat_id, thread_id in settings.get_planka_notification_targets()
        )
        self._dedup_ttl = max(settings.planka_poll_interval_seconds * 4, 30.0)
        self._local_actions: dict[
            tuple[str, str, PlankaNotificationTarget], deque[float]
        ] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @property
    def has_targets(self) -> bool:
        return bool(self._targets)

    @property
    def target_count(self) -> int:
        return len(self._targets)

    async def notify_local_action(
        self,
        bot: Bot,
        text: str,
        *,
        action_type: str,
        card_id: str,
    ) -> None:
        async with self._lock:
            now = time.monotonic()
            self._discard_expired(now)
            for target in self._targets:
                if await self._send(bot, target, text, silent=False):
                    self._local_actions[
                        (action_type, card_id, target)
                    ].append(now + self._dedup_ttl)

    async def notify_polled_action(
        self,
        bot: Bot,
        action: PlankaActionEvent,
        text: str,
        *,
        silent: bool,
    ) -> None:
        async with self._lock:
            self._discard_expired(time.monotonic())
            for target in self._targets:
                key = (action.type, action.card_id, target)
                pending = self._local_actions.get(key)
                if pending:
                    pending.popleft()
                    if not pending:
                        self._local_actions.pop(key, None)
                    continue
                await self._send(bot, target, text, silent=silent)

    async def _send(
        self,
        bot: Bot,
        target: PlankaNotificationTarget,
        text: str,
        *,
        silent: bool,
    ) -> bool:
        try:
            await call_with_retry_after(
                lambda: bot.send_message(
                    chat_id=target.chat_id,
                    text=text,
                    parse_mode="HTML",
                    message_thread_id=target.thread_id,
                    disable_notification=silent,
                    link_preview_options={"is_disabled": True},
                ),
                description=f"Planka notification to {target.chat_id}",
            )
            return True
        except Exception:
            logger.exception(
                "Failed to send Planka notification to %s",
                target.chat_id,
            )
            return False

    def _discard_expired(self, now: float) -> None:
        for key, expirations in tuple(self._local_actions.items()):
            while expirations and expirations[0] <= now:
                expirations.popleft()
            if not expirations:
                self._local_actions.pop(key, None)
