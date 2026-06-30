from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from botka.db.models import UserTier
from botka.handlers.tovoice.commands import (
    _ConversionState,
    _active_conversions,
    _do_tovoice,
    _get_audio_source,
    _is_supported_extension,
    tovoice_handler,
)
from botka.handlers.tovoice.callbacks import tovoice_cancel_callback


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
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    return SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=reply_to,
        audio=audio_obj,
        document=None,
        voice=None,
        chat=SimpleNamespace(id=1),
        message_id=100,
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
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=reply_msg,
        chat=SimpleNamespace(id=1),
        message_id=10,
        bot=bot,
    )

    async def _fake_convert(input_path, output_path, state):
        state.done = True
        state.success = True

    with patch(
        "botka.handlers.tovoice.commands._run_ffmpeg_with_progress",
        side_effect=_fake_convert,
    ), patch(
        "botka.handlers.tovoice.commands.FSInputFile",
        return_value=MagicMock(),
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))

    message.reply_voice.assert_awaited_once()
    # reply() is called once to create the progress message
    message.reply.assert_awaited_once()
    # edit_text called twice: "⏳ Converting…" after download, then "✅ Conversion complete."
    assert progress_msg.edit_text.await_count == 2
    assert "complete" in progress_msg.edit_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_tovoice_success_with_attached_audio() -> None:
    """Successful conversion: audio attached directly to the /tovoice command."""
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_wav", file_name="recording.wav"),
        document=None,
        voice=None,
        chat=SimpleNamespace(id=2),
        message_id=20,
        bot=bot,
    )

    async def _fake_convert(input_path, output_path, state):
        state.done = True
        state.success = True

    with patch(
        "botka.handlers.tovoice.commands._run_ffmpeg_with_progress",
        side_effect=_fake_convert,
    ), patch(
        "botka.handlers.tovoice.commands.FSInputFile",
        return_value=MagicMock(),
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))

    message.reply_voice.assert_awaited_once()
    message.reply.assert_awaited_once()
    # edit_text called twice: "⏳ Converting…" after download, then "✅ Conversion complete."
    assert progress_msg.edit_text.await_count == 2
    assert "complete" in progress_msg.edit_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_tovoice_ffmpeg_failure() -> None:
    """When ffmpeg fails, an error message is shown via the progress message."""
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_flac", file_name="audio.flac"),
        document=None,
        voice=None,
        chat=SimpleNamespace(id=3),
        message_id=30,
        bot=bot,
    )

    async def _fake_convert(input_path, output_path, state):
        state.done = True
        state.success = False

    with patch(
        "botka.handlers.tovoice.commands._run_ffmpeg_with_progress",
        side_effect=_fake_convert,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.resident))

    message.reply_voice.assert_not_awaited()
    # edit_text called twice: "⏳ Converting…" after download, then failure message
    assert progress_msg.edit_text.await_count == 2
    call_args = progress_msg.edit_text.call_args[0][0]
    assert "failed" in call_args.lower() or "conversion" in call_args.lower()


# ---------------------------------------------------------------------------
# Handler: download failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tovoice_download_failure() -> None:
    """When the download fails, an error is shown and no conversion is attempted."""
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    bot = AsyncMock()
    bot.download = AsyncMock(side_effect=Exception("Network error"))
    message = SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_mp3", file_name="song.mp3"),
        document=None,
        voice=None,
        chat=SimpleNamespace(id=5),
        message_id=50,
        bot=bot,
    )

    with patch(
        "botka.handlers.tovoice.commands._run_ffmpeg_with_progress",
    ) as mock_ffmpeg:
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))

    mock_ffmpeg.assert_not_called()
    message.reply_voice.assert_not_awaited()
    # Only one edit_text call: the download failure message
    progress_msg.edit_text.assert_awaited_once()
    call_args = progress_msg.edit_text.call_args[0][0]
    assert "failed" in call_args.lower() or "download" in call_args.lower()


# ---------------------------------------------------------------------------
# Handler: cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tovoice_cancellation() -> None:
    """When cancel is signalled during conversion, the progress message shows cancelled."""
    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    bot = AsyncMock()
    bot.download = AsyncMock()
    message = SimpleNamespace(
        reply=AsyncMock(return_value=progress_msg),
        reply_voice=AsyncMock(),
        reply_to_message=None,
        audio=SimpleNamespace(file_id="fid_mp3", file_name="song.mp3"),
        document=None,
        voice=None,
        chat=SimpleNamespace(id=4),
        message_id=40,
        bot=bot,
    )

    async def _fake_convert(input_path, output_path, state):
        state.cancel.set()
        state.done = True
        state.success = False

    with patch(
        "botka.handlers.tovoice.commands._run_ffmpeg_with_progress",
        side_effect=_fake_convert,
    ):
        await _do_tovoice(message, user_record=SimpleNamespace(tier=UserTier.member))

    message.reply_voice.assert_not_awaited()
    # edit_text called twice: "⏳ Converting…" after download, then cancellation message
    assert progress_msg.edit_text.await_count == 2
    call_args = progress_msg.edit_text.call_args[0][0]
    assert "cancel" in call_args.lower()


# ---------------------------------------------------------------------------
# Cancel callback: tovoice_cancel_callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_callback_active_job() -> None:
    """Cancel callback signals the active conversion to stop."""
    job_id = "99_888"
    cancel_event = asyncio.Event()
    _active_conversions[job_id] = cancel_event

    callback = SimpleNamespace(
        data=f"tovoice_cancel:{job_id}",
        answer=AsyncMock(),
    )

    try:
        await tovoice_cancel_callback(callback)
    finally:
        _active_conversions.pop(job_id, None)

    assert cancel_event.is_set()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_callback_finished_job() -> None:
    """Cancel callback responds gracefully when the job is already done."""
    job_id = "no_such_job"
    # Ensure the job is not in the map
    _active_conversions.pop(job_id, None)

    callback = SimpleNamespace(
        data=f"tovoice_cancel:{job_id}",
        answer=AsyncMock(),
    )

    await tovoice_cancel_callback(callback)

    callback.answer.assert_awaited_once()
    # Should inform user that conversion is already done
    call_kwargs = callback.answer.call_args
    assert call_kwargs is not None


@pytest.mark.asyncio
async def test_cancel_callback_no_data() -> None:
    """Cancel callback handles missing callback data gracefully."""
    callback = SimpleNamespace(
        data=None,
        answer=AsyncMock(),
    )

    await tovoice_cancel_callback(callback)

    callback.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# _ConversionState.percent property
# ---------------------------------------------------------------------------


def test_conversion_state_percent_unknown() -> None:
    state = _ConversionState()
    assert state.percent is None


def test_conversion_state_percent_known() -> None:
    state = _ConversionState(duration_secs=100.0, current_secs=50.0)
    assert state.percent == 50


def test_conversion_state_percent_capped_at_99() -> None:
    state = _ConversionState(duration_secs=10.0, current_secs=10.0)
    assert state.percent == 99


# ---------------------------------------------------------------------------
# Decorated handler: sanity check
# ---------------------------------------------------------------------------


def test_tovoice_handler_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(tovoice_handler)
