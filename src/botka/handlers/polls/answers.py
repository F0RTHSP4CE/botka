from __future__ import annotations

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import PollAnswer
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.polls.utils import (
    build_awaiting_text,
    get_poll_ignored_option_ids,
)
from botka.services.polls_service import PollsService
from botka.services.user_service import UserService

router = Router(name=__name__)


async def _refresh_awaiting_list(
    bot: Bot,
    poll_id: str,
    polls_service: PollsService,
    user_service: UserService,
) -> None:
    poll = await polls_service.get_poll(poll_id)
    if poll is None or poll.awaiting_message_id is None or poll.closed:
        return
    target_users = list(await polls_service.list_target_users(poll.audience))
    voted_ids = await polls_service.list_voted_user_ids(poll_id)
    awaiting_users = [
        user for user in target_users if user.telegram_id not in voted_ids
    ]
    target_ids = {user.telegram_id for user in target_users}
    warning_ids = [user_id for user_id in voted_ids if user_id not in target_ids]
    warning_users = list(await user_service.list_users_by_telegram_ids(warning_ids))
    try:
        await bot.edit_message_text(
            chat_id=poll.chat_id,
            message_id=poll.awaiting_message_id,
            text=build_awaiting_text(awaiting_users, poll.closes_at, warning_users),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.poll_answer()
@inject
async def poll_answer_handler(
    poll_answer: PollAnswer,
    bot: Bot,
    polls_service: FromDishka[PollsService],
    user_service: FromDishka[UserService],
) -> None:
    if poll_answer.user is None:
        return
    poll = await polls_service.get_poll(poll_answer.poll_id)
    if poll is None or poll.closed:
        return
    ignored_option_ids = get_poll_ignored_option_ids(poll_answer.poll_id)
    voted = any(
        option_id not in ignored_option_ids for option_id in poll_answer.option_ids
    )
    await polls_service.set_vote(poll_answer.poll_id, poll_answer.user.id, voted)
    await _refresh_awaiting_list(
        bot,
        poll_answer.poll_id,
        polls_service,
        user_service,
    )
