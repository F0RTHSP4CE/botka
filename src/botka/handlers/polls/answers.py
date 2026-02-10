from __future__ import annotations

from aiogram import Bot, Router
from aiogram.types import PollAnswer
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.polls.utils import (
    cache_poll_ignored_option_ids,
    get_poll_ignored_option_ids,
    refresh_awaiting_message,
)
from botka.services.polls_service import PollsService
from botka.services.user_service import UserService

router = Router(name=__name__)


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
    if not ignored_option_ids:
        ignored_option_ids = await polls_service.get_ignored_option_ids(
            poll_answer.poll_id
        )
        cache_poll_ignored_option_ids(poll_answer.poll_id, ignored_option_ids)
    voted = any(
        option_id not in ignored_option_ids for option_id in poll_answer.option_ids
    )
    await polls_service.set_vote(poll_answer.poll_id, poll_answer.user.id, voted)
    await refresh_awaiting_message(bot, poll, polls_service, user_service)
