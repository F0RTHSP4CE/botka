from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from botka.db.models import User, UserTier

logger = logging.getLogger(__name__)

router = Router(name=__name__)

SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".aiff", ".aif"})


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


async def _convert_to_ogg_opus(input_path: Path, output_path: Path) -> bool:
    """Convert *input_path* to OGG OPUS at *output_path* via ffmpeg.

    Returns True on success.
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
    _, stderr_data = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "ffmpeg conversion failed (exit %d): %s",
            proc.returncode,
            stderr_data.decode(errors="replace").strip(),
        )
        return False
    return True


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

    with tempfile.TemporaryDirectory() as tmp:
        suffix = Path(file_name).suffix.lower() if file_name else ".audio"
        input_path = Path(tmp) / f"input{suffix}"
        output_path = Path(tmp) / "output.ogg"

        await message.bot.download(file_id, destination=str(input_path))

        success = await _convert_to_ogg_opus(input_path, output_path)
        if not success or not output_path.exists():
            await message.reply("Audio conversion failed. Is ffmpeg installed?")
            return

        voice_file = FSInputFile(str(output_path), filename="voice.ogg")
        await message.reply_voice(voice=voice_file)


@router.message(Command("tovoice"))
async def tovoice_handler(
    message: Message,
    user_record: User | None = None,
) -> None:
    await _do_tovoice(message, user_record)
