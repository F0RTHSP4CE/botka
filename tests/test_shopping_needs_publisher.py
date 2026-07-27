from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from botka.services.shopping_needs_publisher import (
    ShoppingNeedsPublisher,
    ShoppingNeedsView,
)


def _publisher(settings) -> ShoppingNeedsPublisher:
    settings.shopping_chat_id = -100123
    settings.shopping_topic_id = 77
    return ShoppingNeedsPublisher(SimpleNamespace(), settings)


def _view(*names: str) -> ShoppingNeedsView:
    return ShoppingNeedsView(
        tuple(
            SimpleNamespace(id=index, text=name)
            for index, name in enumerate(names, start=1)
        )
    )


def test_view_renders_items_and_buy_buttons() -> None:
    view = _view("Milk & bread", "Coffee")

    assert view.text == (
        "<b>Shopping list:</b>\n"
        "- Milk &amp; bread\n"
        "- Coffee"
    )
    assert [row[0].callback_data for row in view.keyboard.inline_keyboard] == [
        "buy:1",
        "buy:2",
    ]


@pytest.mark.asyncio
async def test_publisher_creates_pins_and_updates_one_message(
    settings, monkeypatch
) -> None:
    publisher = _publisher(settings)
    monkeypatch.setattr(
        publisher, "_get_message_id", AsyncMock(side_effect=[None, 456])
    )
    save_message_id = AsyncMock()
    monkeypatch.setattr(publisher, "_save_message_id", save_message_id)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=456)),
        edit_message_text=AsyncMock(),
        pin_chat_message=AsyncMock(),
    )
    first = _view("Milk")
    second = _view("Milk", "Bread")

    created = await publisher.publish(bot, first)
    updated = await publisher.publish(bot, second)

    assert created.link == "https://t.me/c/123/456"
    assert updated == created
    bot.send_message.assert_awaited_once()
    bot.edit_message_text.assert_awaited_once_with(
        chat_id=-100123,
        message_id=456,
        text=second.text,
        parse_mode="HTML",
        reply_markup=second.keyboard,
    )
    save_message_id.assert_awaited_once_with(456)
    bot.pin_chat_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_publisher_recreates_a_missing_message(settings, monkeypatch) -> None:
    publisher = _publisher(settings)
    monkeypatch.setattr(publisher, "_get_message_id", AsyncMock(return_value=456))
    save_message_id = AsyncMock()
    monkeypatch.setattr(publisher, "_save_message_id", save_message_id)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=789)),
        edit_message_text=AsyncMock(
            side_effect=TelegramBadRequest(
                method=EditMessageText(
                    chat_id=-100123,
                    message_id=456,
                    text="Shopping list",
                ),
                message="Bad Request: message to edit not found",
            )
        ),
        pin_chat_message=AsyncMock(),
    )

    publication = await publisher.publish(bot, _view("Milk"))

    assert publication.link == "https://t.me/c/123/789"
    assert save_message_id.await_args_list[0].args == (None,)
    assert save_message_id.await_args_list[1].args == (789,)
