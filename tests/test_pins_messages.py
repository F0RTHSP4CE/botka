from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botka.handlers.pins import messages as pins_messages
from botka.handlers.pins.messages import _copy_or_resend


def _source_message(*, with_video: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=777, username="trackedchat"),
        message_id=42,
        caption="caption",
        text=None,
        caption_entities=None,
        entities=None,
        photo=None,
        animation=None,
        video=SimpleNamespace(file_id="video-file") if with_video else None,
        video_note=None,
        document=None,
        audio=None,
        voice=None,
        sticker=None,
    )


def _source_with_media(media_field: str, value: object) -> SimpleNamespace:
    message = _source_message(with_video=False)
    setattr(message, media_field, value)
    return message


@pytest.mark.asyncio
async def test_canonical_todo_pin_is_not_forwarded() -> None:
    bot = SimpleNamespace(
        id=123,
        copy_message=AsyncMock(),
        send_message=AsyncMock(),
    )
    pinned = SimpleNamespace(
        chat=SimpleNamespace(id=-100123, username=None),
        message_thread_id=77,
        message_id=456,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123, is_bot=True),
        bot=bot,
        pinned_message=pinned,
    )
    borrowed_service = SimpleNamespace(
        list_items_for_message=AsyncMock()
    )
    polls_service = SimpleNamespace(get_poll=AsyncMock())
    needs_publisher = SimpleNamespace(is_canonical_message=AsyncMock())
    todo_publisher = SimpleNamespace(
        is_canonical_message=AsyncMock(return_value=True)
    )

    await pins_messages.pinned_message_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(pins_chat_id=999),
        borrowed_service,
        polls_service,
        needs_publisher,
        todo_publisher,
    )

    todo_publisher.is_canonical_message.assert_awaited_once_with(
        -100123, None, 77, 456
    )
    borrowed_service.list_items_for_message.assert_not_awaited()
    polls_service.get_poll.assert_not_awaited()
    needs_publisher.is_canonical_message.assert_not_awaited()
    bot.copy_message.assert_not_awaited()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_shopping_pin_is_not_forwarded() -> None:
    bot = SimpleNamespace(copy_message=AsyncMock(), send_message=AsyncMock())
    pinned = SimpleNamespace(
        chat=SimpleNamespace(id=-100321, username=None),
        message_thread_id=88,
        message_id=654,
    )
    message = SimpleNamespace(bot=bot, pinned_message=pinned)
    borrowed_service = SimpleNamespace(
        list_items_for_message=AsyncMock()
    )
    polls_service = SimpleNamespace(get_poll=AsyncMock())
    needs_publisher = SimpleNamespace(
        is_canonical_message=AsyncMock(return_value=True)
    )
    todo_publisher = SimpleNamespace(
        is_canonical_message=AsyncMock(return_value=False)
    )

    await pins_messages.pinned_message_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(
            pins_chat_id=999,
            shopping_chat_id=-100321,
            shopping_topic_id=88,
        ),
        borrowed_service,
        polls_service,
        needs_publisher,
        todo_publisher,
    )

    needs_publisher.is_canonical_message.assert_awaited_once_with(
        -100321, 88, 654
    )
    borrowed_service.list_items_for_message.assert_not_awaited()
    bot.copy_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_bot_pin_is_forwarded_as_before() -> None:
    pinned = _source_message(with_video=False)
    pinned.chat.title = "Tracked"
    pinned.chat.full_name = None
    pinned.message_thread_id = 77
    pinned.poll = None
    pinned.media_group_id = None
    pinned.from_user = SimpleNamespace(id=123, full_name="Bot")
    pinned.sender_chat = None
    pinned.text = "Ordinary bot message"
    bot = SimpleNamespace(
        id=123,
        copy_message=AsyncMock(),
        send_message=AsyncMock(),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123, is_bot=True),
        bot=bot,
        pinned_message=pinned,
    )
    borrowed_service = SimpleNamespace(
        list_items_for_message=AsyncMock(return_value=[])
    )
    polls_service = SimpleNamespace(get_poll=AsyncMock())
    needs_publisher = SimpleNamespace(
        is_canonical_message=AsyncMock(return_value=False)
    )
    todo_publisher = SimpleNamespace(
        is_canonical_message=AsyncMock(return_value=False)
    )

    await pins_messages.pinned_message_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(
            pins_chat_id=999,
            shopping_chat_id=None,
            shopping_topic_id=None,
        ),
        borrowed_service,
        polls_service,
        needs_publisher,
        todo_publisher,
    )

    bot.copy_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_copy_or_resend_video_with_keyboard_uses_send_video() -> None:
    bot = SimpleNamespace(
        copy_message=AsyncMock(),
        send_video=AsyncMock(),
    )
    source = _source_message(with_video=True)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://t.me")]]
    )

    await _copy_or_resend(bot, 12345, source, keyboard)

    bot.copy_message.assert_not_called()
    bot.send_video.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_field", "media_value", "expected_method"),
    [
        ("photo", [SimpleNamespace(file_id="photo-file")], "send_photo"),
        ("animation", SimpleNamespace(file_id="animation-file"), "send_animation"),
        ("video", SimpleNamespace(file_id="video-file"), "send_video"),
        ("video_note", SimpleNamespace(file_id="video-note-file"), "send_video_note"),
        ("document", SimpleNamespace(file_id="document-file"), "send_document"),
        ("audio", SimpleNamespace(file_id="audio-file"), "send_audio"),
        ("voice", SimpleNamespace(file_id="voice-file"), "send_voice"),
        ("sticker", SimpleNamespace(file_id="sticker-file"), "send_sticker"),
    ],
)
async def test_copy_or_resend_media_with_keyboard_bypasses_copy(
    media_field: str,
    media_value: object,
    expected_method: str,
) -> None:
    bot = SimpleNamespace(
        copy_message=AsyncMock(),
        send_photo=AsyncMock(),
        send_animation=AsyncMock(),
        send_video=AsyncMock(),
        send_video_note=AsyncMock(),
        send_document=AsyncMock(),
        send_audio=AsyncMock(),
        send_voice=AsyncMock(),
        send_sticker=AsyncMock(),
    )
    source = _source_with_media(media_field, media_value)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://t.me")]]
    )

    await _copy_or_resend(bot, 12345, source, keyboard)

    bot.copy_message.assert_not_called()
    getattr(bot, expected_method).assert_awaited_once()


@pytest.mark.asyncio
async def test_copy_or_resend_video_without_keyboard_prefers_copy_message() -> None:
    bot = SimpleNamespace(
        copy_message=AsyncMock(),
        send_video=AsyncMock(),
    )
    source = _source_message(with_video=True)

    await _copy_or_resend(bot, 12345, source, None)

    bot.copy_message.assert_awaited_once()
    bot.send_video.assert_not_called()
