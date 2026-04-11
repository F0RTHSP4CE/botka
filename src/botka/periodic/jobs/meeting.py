from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from botka.db.models import AgendaTopic, User
from botka.handlers.user_links import format_user_link
from botka.periodic.jobs.base import PeriodicContext
from botka.services.meeting_service import MeetingService

logger = logging.getLogger(__name__)


def _build_message_link(chat_id: int, message_id: int) -> str | None:
    chat_id_str = str(chat_id)
    if not chat_id_str.startswith("-100"):
        return None
    internal_id = chat_id_str.removeprefix("-100")
    return f"https://t.me/c/{internal_id}/{message_id}"


async def send_meeting_agenda(context: PeriodicContext) -> None:
    settings = context.settings
    if settings.meeting_chat_id is None:
        return

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    async with context.sessionmaker() as session:
        service = MeetingService(session)
        topics = await service.get_topics_since(since)

        if not topics:
            return

        lines = [
            "📋 <b>Weekly Meeting Agenda</b>",
            "",
        ]

        user_ids = list({t.user_id for t in topics})
        result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u for u in result.scalars().all()}

        for i, topic in enumerate(topics, 1):
            user = users_by_id.get(topic.user_id)
            if user:
                author = format_user_link(
                    telegram_id=user.telegram_id,
                    username=user.username,
                )
            else:
                author = "Unknown"
            link = _build_message_link(topic.chat_id, topic.message_id)
            if link:
                index = f'<a href="{link}">{i}</a>'
            else:
                index = str(i)
            lines.append(f"{index}. {html.escape(topic.text)} — {author}")

        lines += [
            "",
            "🕘 Meeting starts at 21:00",
        ]
        text = "\n".join(lines)
        sent = await context.bot.send_message(
            chat_id=settings.meeting_chat_id,
            message_thread_id=settings.meeting_topic_id,
            text=text,
            disable_web_page_preview=True,
        )

        try:
            await context.bot.pin_chat_message(
                chat_id=settings.meeting_chat_id,
                message_id=sent.message_id,
            )
        except Exception:
            logger.exception("Failed to pin meeting agenda message")
