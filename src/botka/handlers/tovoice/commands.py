from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import tempfile
from pathlib import Path

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from botka.db.models import User, UserTier

logger = logging.getLogger(__name__)

router = Router(name=__name__)

SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".aiff", ".aif"})

# How often (seconds) the progress message is updated while conversion runs.
_PROGRESS_INTERVAL: float = 5.0

# Regex patterns for parsing ffmpeg stderr output.
_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_RE_TIME = re.compile(r"time=\s*(\d+):(\d+):(\d+\.?\d*)")

# Map of job_id -> asyncio.Event used to signal cancellation.
# Populated while a conversion is in progress; removed when it finishes.
_active_conversions: dict[str, asyncio.Event] = {}


def _get_audio_source(message: Message) -> tuple[str, str | None] | None:
    """Return (file_id, file_name) for the first audio/document found in *message*.

    Returns *None* when no supported attachment is present.
    """
    if message.audio is not None:
        return message.audio.file_id, message.audio.file_name
    if message.document is not None:
        return message.document.file_id, message.document.file_name
    if message.voice is not None:
        return message.voice.file_id, None
    return None


def _is_supported_extension(file_name: str | None) -> bool:
    """Return True when *file_name* has a supported audio extension (or is None,
    which means Telegram already classified the file as audio or voice)."""
    if file_name is None:
        return True
    return Path(file_name).suffix.lower() in SUPPORTED_EXTENSIONS


def _cancel_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"tovoice_cancel:{job_id}")]
        ]
    )


async def _safe_edit(msg: Message, text: str, **kwargs: object) -> None:
    """Edit *msg* text, silently ignoring Telegram errors."""
    try:
        await msg.edit_text(text, **kwargs)  # type: ignore[arg-type]
    except TelegramBadRequest:
        pass
    except Exception:
        logger.debug("Could not edit progress message", exc_info=True)


@dataclasses.dataclass
class _ConversionState:
    cancel: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    duration_secs: float | None = None
    current_secs: float = 0.0
    done: bool = False
    success: bool = False

    @property
    def percent(self) -> int | None:
        if self.duration_secs and self.duration_secs > 0:
            return min(99, int(100 * self.current_secs / self.duration_secs))
        return None


async def _run_ffmpeg_with_progress(
    input_path: Path,
    output_path: Path,
    state: _ConversionState,
) -> None:
    """Run ffmpeg and update *state* with real-time progress.

    Sets ``state.done = True`` and ``state.success`` when finished.
    Kills the process when ``state.cancel`` is set.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        "-ar",
        "48000",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    assert proc.stderr is not None
    stderr_buf = bytearray()

    async def _read_stderr() -> None:
        while True:
            chunk = await proc.stderr.read(512)  # type: ignore[union-attr]
            if not chunk:
                break
            stderr_buf.extend(chunk)
            text = chunk.decode(errors="replace")
            if state.duration_secs is None:
                m = _RE_DURATION.search(stderr_buf.decode(errors="replace"))
                if m:
                    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    state.duration_secs = h * 3600 + mn * 60 + s
            for m in _RE_TIME.finditer(text):
                h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                state.current_secs = h * 3600 + mn * 60 + s

    async def _watch_cancel() -> None:
        await state.cancel.wait()
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    read_task = asyncio.create_task(_read_stderr())
    cancel_task = asyncio.create_task(_watch_cancel())
    try:
        await proc.wait()
    finally:
        read_task.cancel()
        cancel_task.cancel()
        await asyncio.gather(read_task, cancel_task, return_exceptions=True)

    state.done = True
    if state.cancel.is_set():
        state.success = False
        return

    if proc.returncode != 0:
        logger.warning(
            "ffmpeg conversion failed (exit %d): %s",
            proc.returncode,
            stderr_buf.decode(errors="replace").strip(),
        )
        state.success = False
    else:
        state.success = True


async def _do_tovoice(message: Message, user_record: User | None) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can use /tovoice.")
        return

    # Determine the source message: reply has priority, otherwise the command
    # message itself (when the audio is attached to the /tovoice message).
    source_message = (
        message.reply_to_message
        if message.reply_to_message is not None
        else message
    )

    result = _get_audio_source(source_message)
    if result is None:
        await message.reply(
            "Please reply to an audio file or attach one to the /tovoice command.\n"
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
        return

    file_id, file_name = result
    if not _is_supported_extension(file_name):
        await message.reply(
            f"Unsupported file format. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
        return

    job_id = f"{message.chat.id}_{message.message_id}"
    cancel_event = asyncio.Event()
    _active_conversions[job_id] = cancel_event

    try:
        progress_msg = await message.reply(
            "⏳ Downloading…",
            reply_markup=_cancel_keyboard(job_id),
        )

        with tempfile.TemporaryDirectory() as tmp:
            suffix = Path(file_name).suffix.lower() if file_name else ".audio"
            input_path = Path(tmp) / f"input{suffix}"
            output_path = Path(tmp) / "output.ogg"

            try:
                await message.bot.download(file_id, destination=str(input_path))
            except Exception as exc:
                logger.warning("Failed to download file %s: %s", file_id, exc)
                await _safe_edit(progress_msg, "❌ Failed to download the file.")
                return

            await _safe_edit(
                progress_msg, "⏳ Converting…", reply_markup=_cancel_keyboard(job_id)
            )

            state = _ConversionState(cancel=cancel_event)
            conv_task = asyncio.create_task(
                _run_ffmpeg_with_progress(input_path, output_path, state)
            )

            async def _update_loop() -> None:
                try:
                    while True:
                        await asyncio.sleep(_PROGRESS_INTERVAL)
                        if state.done:
                            return
                        pct = state.percent
                        status = (
                            f"⏳ Converting… {pct}%"
                            if pct is not None
                            else "⏳ Converting…"
                        )
                        await _safe_edit(
                            progress_msg, status, reply_markup=_cancel_keyboard(job_id)
                        )
                except asyncio.CancelledError:
                    return

            update_task = asyncio.create_task(_update_loop())
            try:
                await conv_task
            finally:
                update_task.cancel()
                await asyncio.gather(update_task, return_exceptions=True)

            if cancel_event.is_set():
                await _safe_edit(progress_msg, "❌ Conversion cancelled.")
                return

            if not state.success or not output_path.exists():
                await _safe_edit(
                    progress_msg, "❌ Conversion failed. Is ffmpeg installed?"
                )
                return

            voice_file = FSInputFile(str(output_path), filename="voice.ogg")
            await message.reply_voice(voice=voice_file)
            await _safe_edit(progress_msg, "✅ Conversion complete.")
    finally:
        _active_conversions.pop(job_id, None)


@router.message(Command("tovoice"))
async def tovoice_handler(
    message: Message,
    user_record: User | None = None,
) -> None:
    await _do_tovoice(message, user_record)
