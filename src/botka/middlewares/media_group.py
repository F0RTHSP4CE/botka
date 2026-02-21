from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


class MediaGroupCollectorMiddleware(BaseMiddleware):
    """Collect media-group (album) messages and forward them all to the handler at once.

    Injects ``data["album"]`` — a list of all :class:`~aiogram.types.Message` objects
    belonging to the same media group.  Only the *first* message in a group triggers
    the actual handler call; subsequent messages are accumulated silently.

    Non-album messages pass through immediately without any delay.
    """

    def __init__(self, latency: float = 0.3) -> None:
        self._latency = latency
        self._groups: dict[str, list[Message]] = {}
        self._pending: dict[str, asyncio.Task[None]] = {}
        # Handler + data captured from the FIRST message of each group (before any
        # middleware resolved dishka/user_sync into data), so calling the chain later
        # creates a fresh request scope.
        self._first_ctx: dict[str, tuple[Callable[..., Awaitable[Any]], dict[str, Any]]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        gid = event.media_group_id
        is_first = gid not in self._groups
        self._groups.setdefault(gid, []).append(event)

        if is_first:
            self._first_ctx[gid] = (handler, data)

        # Reset the flush timer on every new message in the group.
        existing = self._pending.pop(gid, None)
        if existing and not existing.done():
            existing.cancel()

        async def _flush(group_id: str) -> None:
            await asyncio.sleep(self._latency)
            messages = self._groups.pop(group_id, [])
            self._pending.pop(group_id, None)
            ctx = self._first_ctx.pop(group_id, None)
            if not messages or ctx is None:
                return
            first_handler, first_data = ctx
            first_data["album"] = messages
            try:
                await first_handler(messages[0], first_data)
            except Exception:
                logger.exception("MediaGroupCollectorMiddleware: error handling group %s", group_id)

        self._pending[gid] = asyncio.create_task(_flush(gid))
        return None
