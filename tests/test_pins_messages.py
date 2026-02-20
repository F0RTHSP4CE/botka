from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
