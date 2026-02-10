from __future__ import annotations

from datetime import datetime, timezone

from botka.periodic.jobs.base import PeriodicContext
from botka.handlers.polls.utils import format_close_time, refresh_awaiting_message
from botka.services.polls_service import PollsService
from botka.services.user_service import UserService


async def poll_maintenance(context: PeriodicContext) -> None:
    now = datetime.now(timezone.utc)
    async with context.sessionmaker() as session:
        service = PollsService(session)
        user_service = UserService(session, context.settings)
        due_polls = await service.list_due_polls(now)
        for poll in due_polls:
            try:
                await context.bot.stop_poll(
                    chat_id=poll.chat_id,
                    message_id=poll.message_id,
                )
            except Exception:
                pass
            await service.mark_closed(poll.poll_id)
            if poll.awaiting_message_id is not None:
                start_at = poll.created_at
                if start_at.tzinfo is None:
                    start_at = start_at.replace(tzinfo=timezone.utc)
                elapsed_label = _format_elapsed_duration(start_at, now)
                message = (
                    "Poll closed (auto).\n"
                    f"Start: {format_close_time(start_at)}\n"
                    f"End: {format_close_time(now)}\n"
                    f"Closed after {elapsed_label}."
                )
                try:
                    await context.bot.edit_message_text(
                        chat_id=poll.chat_id,
                        message_id=poll.awaiting_message_id,
                        text=message,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass
        active_polls = await service.list_active_polls(now)
        for poll in active_polls:
            try:
                await refresh_awaiting_message(
                    context.bot,
                    poll,
                    service,
                    user_service,
                )
            except Exception:
                pass


def _format_elapsed_duration(start_at: datetime, end_at: datetime) -> str:
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    delta = end_at - start_at
    total_seconds = max(int(delta.total_seconds()), 0)
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    if days == 0 and hours == 0:
        return "less than 1 hour"
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or not parts:
        parts.append(f"{hours}h")
    return " ".join(parts)
