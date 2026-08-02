from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User

from botka.middlewares.user_sync import UserSyncMiddleware


@pytest.mark.asyncio
async def test_anonymous_admin_fake_user_is_not_synchronized(settings) -> None:
    chat = Chat(id=-100123, type=ChatType.SUPERGROUP, title="Test chat")
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=User(
            id=1087968824,
            is_bot=True,
            first_name="GroupAnonymousBot",
        ),
        sender_chat=chat,
        text="/deanon",
    )
    sessionmaker = Mock()
    next_handler = AsyncMock(return_value="handled")
    data: dict = {}
    middleware = UserSyncMiddleware(sessionmaker, settings)

    result = await middleware(next_handler, message, data)

    assert result == "handled"
    sessionmaker.assert_not_called()
    assert "user_record" not in data
    next_handler.assert_awaited_once_with(message, data)
