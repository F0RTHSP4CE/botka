from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.polls.utils import format_close_time
from botka.services.polls_service import PollsService

router = Router(name=__name__)


@router.callback_query(F.data.startswith("poll_close:"))
@inject
async def close_poll_callback(
    callback: CallbackQuery,
    polls_service: FromDishka[PollsService],
) -> None:
    poll_id = callback.data.split(":", 1)[1]
    poll = await polls_service.get_poll(poll_id)
    if poll is None:
        await callback.answer("Poll not found.", show_alert=True)
        return
    if callback.from_user.id != poll.author_telegram_id:
        await callback.answer("Only the author can close this poll.", show_alert=True)
        return
    await callback.bot.stop_poll(chat_id=poll.chat_id, message_id=poll.message_id)
    await polls_service.mark_closed(poll_id)
    if poll.awaiting_message_id is not None:
        now = datetime.now(timezone.utc)
        if now < poll.closes_at:
            close_label = format_close_time(poll.closes_at)
            text = f"Poll closed early by author. Scheduled close was {close_label}."
        else:
            text = "Poll closed."
        await callback.bot.edit_message_text(
            chat_id=poll.chat_id,
            message_id=poll.awaiting_message_id,
            text=text,
            disable_web_page_preview=True,
        )
    await callback.answer("Poll closed.")
