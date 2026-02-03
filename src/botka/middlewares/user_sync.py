from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PollAnswer
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings
from botka.services.user_service import UserService


class UserSyncMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker, settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        elif isinstance(event, PollAnswer):
            user = event.user
        if user is not None:
            async with self._sessionmaker() as session:
                user_service = UserService(session, self._settings)
                await user_service.ensure_user(user.id, user.username)
                data["user_record"] = await user_service.get_user(user.id)
        return await handler(event, data)
