from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText
from botka.services.planka_command_service import CardEntry
from botka.services.planka_todo_publisher import (
    PlankaTodoPublisher,
    TodoTarget,
    TodoView,
)


def _publisher(settings) -> PlankaTodoPublisher:
    settings.planka_notification_chat_ids = "-100123:77"
    return PlankaTodoPublisher(SimpleNamespace(), settings)


def _view(name: str) -> TodoView:
    return TodoView(
        (
            CardEntry(
                short_id=1,
                card_id="card-1",
                name=name,
                has_images=False,
                has_other_attachments=False,
            ),
        ),
        (),
    )


@pytest.mark.asyncio
async def test_publisher_creates_pins_and_then_updates_canonical_message(
    settings, monkeypatch
) -> None:
    publisher = _publisher(settings)
    monkeypatch.setattr(
        publisher, "_get_message_id", AsyncMock(side_effect=[None, 456])
    )
    monkeypatch.setattr(publisher, "_save_message_id", AsyncMock())
    sent = SimpleNamespace(message_id=456)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=sent),
        edit_message_text=AsyncMock(),
        pin_chat_message=AsyncMock(),
    )
    first = _view("First")
    second = _view("Second")
    first_links = await publisher.publish(bot, first)
    second_links = await publisher.publish(bot, second)

    assert first_links.links == ("https://t.me/c/123/456",)
    assert second_links == first_links
    bot.send_message.assert_awaited_once()
    bot.edit_message_text.assert_awaited_once_with(
        chat_id="-100123",
        message_id=456,
        text=second.text,
        parse_mode="HTML",
        reply_markup=second.keyboard,
        link_preview_options={"is_disabled": True},
    )
    bot.pin_chat_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_publisher_waits_and_retries_flood_limited_edit(
    settings, monkeypatch
) -> None:
    publisher = _publisher(settings)
    monkeypatch.setattr(publisher, "_get_message_id", AsyncMock(return_value=456))
    sleep = AsyncMock()
    monkeypatch.setattr(
        "botka.services.telegram_retry.asyncio.sleep",
        sleep,
    )
    retry_after = TelegramRetryAfter(
        method=EditMessageText(
            chat_id="-100123",
            message_id=456,
            text="Updated",
        ),
        message="Too Many Requests",
        retry_after=23,
    )
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=[retry_after, None]),
        pin_chat_message=AsyncMock(),
    )

    result = await publisher.publish(bot, _view("Updated"))

    assert result.links == ("https://t.me/c/123/456",)
    assert bot.edit_message_text.await_count == 2
    sleep.assert_awaited_once_with(23)
    bot.pin_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publisher_recreates_missing_stored_message(
    settings, monkeypatch
) -> None:
    publisher = _publisher(settings)
    monkeypatch.setattr(
        publisher, "_get_message_id", AsyncMock(side_effect=[None, 456])
    )
    save_message_id = AsyncMock()
    monkeypatch.setattr(publisher, "_save_message_id", save_message_id)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[
                SimpleNamespace(message_id=456),
                SimpleNamespace(message_id=789),
            ]
        ),
        edit_message_text=AsyncMock(),
        pin_chat_message=AsyncMock(),
    )
    await publisher.publish(bot, _view("First"))
    bot.edit_message_text.side_effect = TelegramBadRequest(
        method=EditMessageText(
            chat_id="-100123",
            message_id=456,
            text="Second",
        ),
        message="Bad Request: message to edit not found",
    )

    links = await publisher.publish(bot, _view("Second"))

    assert links.links == ("https://t.me/c/123/789",)
    assert bot.send_message.await_count == 2
    target = TodoTarget("-100123", 77)
    assert save_message_id.await_args_list[-2].args == (target, None)
    assert save_message_id.await_args_list[-1].args == (target, 789)
