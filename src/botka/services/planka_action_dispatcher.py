from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from botka.config import Settings
from botka.services.planka_album_tracker import PlankaAlbumTracker
from botka.services.planka_client import PlankaClient
from botka.services.planka_command_service import PlankaCommandService
from botka.services.planka_mappings_service import PlankaCardMappingService
from botka.services.planka_notification_service import PlankaNotificationService
from botka.services.planka_todo_publisher import PlankaTodoPublisher

logger = logging.getLogger(__name__)
_REFRESH_DEBOUNCE_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class LocalActionNotification:
    text: str
    action_type: str
    card_id: str


class PlankaActionDispatcher:
    """Run post-action notifications and list refreshes outside request scopes."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        planka: PlankaClient,
        tracker: PlankaAlbumTracker,
        notifications: PlankaNotificationService,
        todo_publisher: PlankaTodoPublisher,
        *,
        refresh_debounce_seconds: float = _REFRESH_DEBOUNCE_SECONDS,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._planka = planka
        self._tracker = tracker
        self._notifications = notifications
        self._todo_publisher = todo_publisher
        self._refresh_debounce_seconds = refresh_debounce_seconds
        self._tasks: set[asyncio.Task[None]] = set()
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_generation = 0

    def dispatch(
        self,
        bot: Bot,
        notification: LocalActionNotification | None = None,
    ) -> None:
        self._refresh_generation += 1
        if self._refresh_task is None:
            self._refresh_task = self._start(self._refresh_loop(bot))
        if notification is not None:
            self._start(self._notify(bot, notification))

    def _start(
        self, coroutine: Coroutine[Any, Any, None]
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _notify(
        self,
        bot: Bot,
        notification: LocalActionNotification,
    ) -> None:
        try:
            await self._notifications.notify_local_action(
                bot,
                notification.text,
                action_type=notification.action_type,
                card_id=notification.card_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background Planka notification failed")

    async def _refresh_loop(self, bot: Bot) -> None:
        try:
            while True:
                generation = self._refresh_generation
                await asyncio.sleep(self._refresh_debounce_seconds)
                if generation != self._refresh_generation:
                    continue
                await self._refresh_todo(bot)
                if generation == self._refresh_generation:
                    return
        finally:
            self._refresh_task = None

    async def _refresh_todo(self, bot: Bot) -> None:
        async with self._sessionmaker() as session:
            svc = PlankaCommandService(
                self._planka,
                PlankaCardMappingService(session),
                self._settings,
                self._tracker,
            )
            await self._todo_publisher.refresh_safely(bot, svc)
