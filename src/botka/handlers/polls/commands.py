from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import InputPollOption, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.handlers.polls.messages import create_managed_poll
from botka.handlers.polls.utils import format_close_time, parse_poll_question
from botka.handlers.user_links import format_user_link
from botka.services.polls_service import PollsService

router = Router(name=__name__)

DEFAULT_POLL_OPTIONS = ("Yes", "No", "See results")
MAX_POLL_QUESTION_LENGTH = 300
MAX_POLL_OPTION_LENGTH = 100
MAX_POLL_OPTIONS = 12


@router.message(Command("poll"))
@inject
async def poll_create_handler(
    message: Message,
    command: CommandObject,
    polls_service: FromDishka[PollsService],
    settings: FromDishka[Settings],
    user_record: User | None = None,
) -> None:
    await create_poll_from_command(
        message, command, polls_service, settings, user_record=user_record
    )


async def create_poll_from_command(
    message: Message,
    command: CommandObject,
    polls_service: PollsService,
    settings: Settings,
    *,
    user_record: User | None,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if user_record is None or user_record.tier != UserTier.resident:
        await message.reply("Only residents can create polls.")
        return

    command_parts = _parse_poll_command_args(command.args or "")
    if command_parts is None:
        await message.reply(
            "Poll options must be written on separate lines starting with "
            "<code>- </code>, with at least two options."
        )
        return

    question, custom_options = command_parts
    parsed = parse_poll_question(f"!{question}")
    if parsed is None:
        await message.reply(
            "Usage: /poll [residents|members|everyone] &lt;question&gt;"
        )
        return
    option_texts = custom_options or list(DEFAULT_POLL_OPTIONS)
    if len(option_texts) > MAX_POLL_OPTIONS:
        await message.reply("A poll can have at most 12 options.")
        return
    if any(len(option) > MAX_POLL_OPTION_LENGTH for option in option_texts):
        await message.reply("Each poll option can have at most 100 characters.")
        return
    if len(parsed.display_question) > MAX_POLL_QUESTION_LENGTH:
        await message.reply(
            "Poll question is too long (maximum 300 characters, including the audience tag)."
        )
        return

    await create_managed_poll(
        message,
        parsed=parsed,
        options=[InputPollOption(text=text) for text in option_texts],
        option_texts=option_texts,
        polls_service=polls_service,
        settings=settings,
        delete_source_message=False,
    )


@router.message(Command("poll_close"))
@inject
async def poll_close_handler(
    message: Message,
    command: CommandObject,
    polls_service: FromDishka[PollsService],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return

    poll = None
    poll_id = None

    reply = message.reply_to_message
    if reply is not None and reply.poll is not None:
        poll_id = reply.poll.id
        poll = await polls_service.get_poll(poll_id)
    elif reply is not None:
        poll = await polls_service.get_poll_by_message_id(
            message.chat.id, reply.message_id
        )
        if poll is None:
            poll = await polls_service.get_poll_by_awaiting_message_id(
                message.chat.id, reply.message_id
            )
        if poll is not None:
            poll_id = poll.poll_id
    else:
        args = (command.args or "").strip()
        if not args:
            args = _extract_poll_close_args(message.text)
        if args:
            poll_id = args
            poll = await polls_service.get_poll(poll_id)

    if poll is None or poll_id is None:
        await message.reply(
            "Poll not found. Reply to the poll (or awaiting message) or use /poll_close &lt;poll_id&gt;."
        )
        return

    if message.from_user.id != poll.author_telegram_id:
        await message.reply("Only the author can close this poll.")
        return

    if poll.closed:
        await message.reply("Poll already closed.")
        return

    try:
        await message.bot.stop_poll(chat_id=poll.chat_id, message_id=poll.message_id)
    except TelegramBadRequest:
        await message.reply(
            "Failed to close poll. Bot might lack permissions or poll is already closed."
        )
        return
    await polls_service.mark_closed(poll.poll_id)

    if poll.awaiting_message_id is not None:
        now = datetime.now(timezone.utc)
        now_label = format_close_time(now)
        author_link = format_user_link(user=message.from_user)
        closes_at = poll.closes_at
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if now < closes_at:
            close_label = format_close_time(closes_at)
            text = f"Poll closed early by {author_link} at {now_label}. Scheduled close was {close_label}."
        else:
            text = f"Poll closed by {author_link} at {now_label}."
        try:
            await message.bot.edit_message_text(
                chat_id=poll.chat_id,
                message_id=poll.awaiting_message_id,
                text=text,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            pass

    await message.reply("Poll closed.")


def _extract_poll_close_args(text: str | None) -> str:
    if not text:
        return ""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return ""
    command = parts[0].lstrip("/")
    if command.startswith("poll_close"):
        return parts[1].strip() if len(parts) > 1 else ""
    return ""


def _parse_poll_command_args(raw_args: str) -> tuple[str, list[str] | None] | None:
    lines = raw_args.strip().splitlines()
    if not lines:
        return "", None

    question = lines[0].strip()
    option_lines = [line.strip() for line in lines[1:] if line.strip()]
    if not option_lines:
        return question, None

    options: list[str] = []
    for line in option_lines:
        if not line.startswith("-"):
            return None
        option = line[1:].strip()
        if not option:
            return None
        options.append(option)

    if len(options) < 2:
        return None
    return question, options
