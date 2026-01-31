from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.services.polls_service import PollsService


async def poll_autoclose_loop(
    bot: Bot,
    sessionmaker: async_sessionmaker,
    *,
    interval_seconds: int = 3600,
) -> None:
    while True:
        now = datetime.now(timezone.utc)
        async with sessionmaker() as session:
            service = PollsService(session)
            due_polls = await service.list_due_polls(now)
            for poll in due_polls:
                try:
                    await bot.stop_poll(
                        chat_id=poll.chat_id, message_id=poll.message_id
                    )
                except Exception:
                    pass
                await service.mark_closed(poll.poll_id)
                if poll.awaiting_message_id is not None:
                    try:
                        await bot.edit_message_text(
                            chat_id=poll.chat_id,
                            message_id=poll.awaiting_message_id,
                            text="Poll closed (auto).",
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass
        await asyncio.sleep(interval_seconds)
