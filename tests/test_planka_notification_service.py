from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from botka.services.planka_client import PlankaActionEvent
from botka.services.planka_notification_service import PlankaNotificationService


@pytest.mark.asyncio
async def test_locally_sent_action_is_not_duplicated_by_poller(settings) -> None:
    settings.planka_notification_chat_ids = "-100123:77"
    service = PlankaNotificationService(settings)
    bot = SimpleNamespace(send_message=AsyncMock())
    action = PlankaActionEvent(
        id="action-1",
        type="moveCard",
        card_id="card-1",
        card_name="Connect cable",
        user_id="planka-user",
    )

    await service.notify_local_action(
        bot,
        "✅ Complete",
        action_type="moveCard",
        card_id="card-1",
    )
    await service.notify_polled_action(
        bot,
        action,
        "✅ Complete",
        silent=False,
    )

    bot.send_message.assert_awaited_once_with(
        chat_id="-100123",
        text="✅ Complete",
        parse_mode="HTML",
        message_thread_id=77,
        disable_notification=False,
        link_preview_options={"is_disabled": True},
    )


@pytest.mark.asyncio
async def test_notification_honors_telegram_retry_after(
    settings, monkeypatch
) -> None:
    settings.planka_notification_chat_ids = "-100123:77"
    service = PlankaNotificationService(settings)
    sleep = AsyncMock()
    monkeypatch.setattr("botka.services.telegram_retry.asyncio.sleep", sleep)
    retry_after = TelegramRetryAfter(
        method=SendMessage(chat_id="-100123", text="✅ Complete"),
        message="Too Many Requests",
        retry_after=12,
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[retry_after, None])
    )

    await service.notify_local_action(
        bot,
        "✅ Complete",
        action_type="moveCard",
        card_id="card-1",
    )

    assert bot.send_message.await_count == 2
    sleep.assert_awaited_once_with(12)
