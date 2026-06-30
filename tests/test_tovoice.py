from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from botka.db.models import UserTier
from botka.handlers.tovoice.commands import (
    _do_tovoice,
    _get_audio_source,
    _is_supported_extension,
    tovoice_handler,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_is_supported_extension_none():
    assert _is_supported_extension(None) is True


def test_is_supported_extension_mp3():
    assert _is_supported_extension("song.mp3") is True


def test_is_supported_extension_uppercase_mp3():
    assert _is_supported_extension("song.MP3") is True


def test_is_supported_extension_wav():
    assert _is_supported_extension("audio.wav") is True


def test_is_supported_extension_flac():
    assert _is_supported_extension("track.flac") is True


def test_is_supported_extension_aiff():
    assert _is_supported_extension("track.aiff") is True


def test_is_supported_extension_aif():
    assert _is_supported_extension("track.aif") is True


def test_is_supported_extension_unsupported():
    assert _is_supported_extension("video.mp4") is False


def test_is_supported_extension_txt():
    assert _is_supported_extension("file.txt") is False


def test_get_audio_source_audio():
    msg = SimpleNamespace(
        audio=SimpleNamespace(file_id="file1", file_name="song.mp3"),
        document=None,
        voice=None,
    )
    assert _get_audio_source(msg) == ("file1", "song.mp3")


def test_get_audio_source_document():
    msg = SimpleNamespace(
        audio=None,
        document=SimpleNamespace(file_id="file2", file_name="audio.wav"),
        voice=None,
    )
    assert _get_audio_source(msg) == ("file2", "audio.wav")


def test_get_audio_source_voice():
    msg = SimpleNamespace(
        audio=None,
        document=None,
        voice=SimpleNamespace(file_id="file3"),
    )
    assert _get_audio_source(msg) == ("file3", None)


def test_get_audio_source_none():
    msg = SimpleNamespace(audio=None, document=None, voice=None)
    assert _get_audio_source(msg) is None


# ---------------------------------------------------------------------------
# Handler: access control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tovoice_rejects_guest_user() -> None:
    message = SimpleNamespace(reply=AsyncMock())
    await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.guest))
    message.reply.assert_awaited_once_with(
        "Only residents and members can use /tovoice."
    )


@pytest.mark.asyncio
async def test_tovoice_rejects_none_user() -> None:
    message = SimpleNamespace(reply=AsyncMock())
    await _do_tovoice(message, user_record=None)
    message.reply.assert_awaited_once_with(
        "Only residents and members can use /tovoice."
    )


# ---------------------------------------------------------------------------
# Handler: input validation
# ---------------------------------------------------------------------------


def _make_message_with_audio(audio_obj, reply_to=None):
    return SimpleNamespace(
        reply=AsyncMock(),
        reply_voice=AsyncMock(),
        reply_to_message=reply_to,
        audio=audio_obj,
        document=None,
        voice=None,
        bot=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_tovoice_no_audio_file() -> None:
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_to_message=None,
        audio=None,
        document=None,
        voice=None,
    )
    await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))
    assert message.reply.await_count == 1
    call_args = message.reply.call_args[0][0]
    assert "reply to an audio file" in call_args.lower() or "attach" in call_args.lower()


@pytest.mark.asyncio
async def test_tovoice_reply_no_audio_file() -> None:
    reply_msg = SimpleNamespace(audio=None, document=None, voice=None)
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_to_message=reply_msg,
    )
    await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))
    message.reply.assert_awaited_once()
    call_args = message.reply.call_args[0][0]
    assert "reply to an audio file" in call_args.lower() or "attach" in call_args.lower()


@pytest.mark.asyncio
async def test_tovoice_unsupported_document_extension() -> None:
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_to_message=None,
        audio=None,
        document=SimpleNamespace(file_id="id1", file_name="video.mp4"),
        voice=None,
    )
    await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))
    message.reply.assert_awaited_once()
    call_args = message.reply.call_args[0][0]
    assert "unsupported" in call_args.lower()


@pytest.mark.asyncio
async def test_tovoice_unsupported_extension_in_reply() -> None:
    reply_msg = SimpleNamespace(
        audio=None,
        document=SimpleNamespace(file_id="id1", file_name="notes.pdf"),
        voice=None,
    )
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_to_message=reply_msg,
    )
    await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))
    message.reply.assert_awaited_once()
    call_args = message.reply.call_args[0][0]
    assert "unsupported" in call_args.lower()


# ---------------------------------------------------------------------------
# Handler: successful conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tovoice_success_with_reply() -> None:
    """Successful conversion: reply to an MP3 audio message."""
    reply_msg = SimpleNamespace(
        audio=SimpleNamespace(file_id="fid_mp3", file_name="song.mp3"),
        document=None,
        voice=None,
    )
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_voice=AsyncMock(),
        reply_to_message=reply_msg,
        bot=bot,
    )

    with patch(
        "botka.handlers.tovoice.commands._convert_to_ogg_opus",
        new=AsyncMock(return_value=True),
    ), patch(
        "botka.handlers.tovoice.commands.FSInputFile",
        return_value=MagicMock(),
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))

    message.reply_voice.assert_awaited_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_tovoice_success_with_attached_audio() -> None:
    """Successful conversion: audio attached directly to the /tovoice command."""
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_wav", file_name="recording.wav"),
        document=None,
        voice=None,
        bot=bot,
    )

    with patch(
        "botka.handlers.tovoice.commands._convert_to_ogg_opus",
        new=AsyncMock(return_value=True),
    ), patch(
        "botka.handlers.tovoice.commands.FSInputFile",
        return_value=MagicMock(),
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))

    message.reply_voice.assert_awaited_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_tovoice_ffmpeg_failure() -> None:
    """When ffmpeg fails, an error message is sent."""
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_flac", file_name="audio.flac"),
        document=None,
        voice=None,
        bot=bot,
    )

    with patch(
        "botka.handlers.tovoice.commands._convert_to_ogg_opus",
        new=AsyncMock(return_value=False),
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))

    message.reply.assert_awaited_once()
    call_args = message.reply.call_args[0][0]
    assert "conversion failed" in call_args.lower()
    message.reply_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# Decorated handler: sanity check
# ---------------------------------------------------------------------------


def test_tovoice_handler_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(tovoice_handler)
