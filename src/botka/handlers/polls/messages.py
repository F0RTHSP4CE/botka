from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import PollType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputPollOption, InputPollOptionUnion, Message
from typing import cast
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.polls.utils import (
    build_awaiting_text,
    build_close_keyboard,
    parse_poll_question,
    poll_close_at,
)
from botka.services.polls_service import PollsService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.message(F.poll)
@inject
async def poll_message_handler(
    message: Message,
    polls_service: FromDishka[PollsService],
    user_service: FromDishka[UserService],
) -> None:
    if message.poll is None or message.from_user is None:
        return
    parsed = parse_poll_question(message.poll.question)
    if parsed is None:
        return
    await user_service.ensure_user(message.from_user.id, message.from_user.username)
    options = [InputPollOption(text=option.text) for option in message.poll.options]
    new_poll = await message.bot.send_poll(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        question=parsed.display_question,
        options=cast(list[InputPollOptionUnion], options),
        is_anonymous=False,
        allows_multiple_answers=message.poll.allows_multiple_answers,
        type=message.poll.type,
        correct_option_id=(
            message.poll.correct_option_id
            if message.poll.type == PollType.QUIZ
            else None
        ),
        explanation=(
            message.poll.explanation if message.poll.type == PollType.QUIZ else None
        ),
    )
    if new_poll.poll is None:
        return
    closes_at = poll_close_at(datetime.now(timezone.utc))
    target_users = list(await polls_service.list_target_users(parsed.audience))
    awaiting_text = build_awaiting_text(target_users, closes_at)
    awaiting_message = await message.bot.send_message(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=awaiting_text,
        reply_to_message_id=new_poll.message_id,
        reply_markup=build_close_keyboard(new_poll.poll.id),
        disable_web_page_preview=True,
    )
    await polls_service.create_poll(
        poll_id=new_poll.poll.id,
        chat_id=new_poll.chat.id,
        message_id=new_poll.message_id,
        author_telegram_id=message.from_user.id,
        question=parsed.display_question,
        audience=parsed.audience,
        awaiting_message_id=awaiting_message.message_id,
        closes_at=closes_at,
    )
    await message.bot.pin_chat_message(
        chat_id=message.chat.id,
        message_id=new_poll.message_id,
        disable_notification=True,
    )
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
