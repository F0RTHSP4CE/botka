from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.polls.utils import format_close_time
from botka.handlers.user_links import format_user_link
from botka.services.polls_service import PollsService

router = Router(name=__name__)


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
