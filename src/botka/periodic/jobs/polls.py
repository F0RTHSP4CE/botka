from __future__ import annotations

from datetime import datetime, timezone
import html

from aiogram.types import Poll as TelegramPoll

from botka.handlers.user_links import format_user_link
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
            # Re-check status to avoid posting decisions for polls closed via command.
            fresh_poll = await service.get_poll(poll.poll_id)
            if fresh_poll is None or fresh_poll.closed:
                continue
            poll = fresh_poll
            poll_result = None
            try:
                poll_result = await context.bot.stop_poll(
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
                author_users = await user_service.list_users_by_telegram_ids(
                    [poll.author_telegram_id]
                )
                author_user = author_users[0] if author_users else None
                author_link = format_user_link(
                    telegram_id=poll.author_telegram_id,
                    username=author_user.username if author_user else None,
                )
                message = (
                    "Poll closed (auto).\n"
                    f"Author: {author_link}\n"
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
            await _post_poll_decision(context, service, poll, poll_result)
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


async def _post_poll_decision(
    context: PeriodicContext,
    service: PollsService,
    poll,
    poll_result: TelegramPoll | None,
) -> None:
    if context.settings.decisions_chat_id is None:
        return
    ignored_option_ids = await service.get_ignored_option_ids(poll.poll_id)
    counts = _count_votes_from_poll_result(poll_result, ignored_option_ids)
    if not counts:
        target_users = list(await service.list_target_users(poll.audience))
        target_ids = {user.telegram_id for user in target_users}
        if not target_ids:
            return
        option_votes = await service.list_option_votes(poll.poll_id, target_ids)
        counts = {}
        for option_id in option_votes:
            if option_id in ignored_option_ids:
                continue
            counts[option_id] = counts.get(option_id, 0) + 1
    if not counts:
        return
    max_count = max(counts.values())
    winners = [option_id for option_id, count in counts.items() if count == max_count]
    if len(winners) != 1:
        return
    winning_id = winners[0]
    option_text = await _resolve_option_text(
        service,
        poll.poll_id,
        poll_result,
        winning_id,
    )
    if option_text is None:
        return
    winning_votes = counts[winning_id]
    poll_link = _build_poll_link(poll.chat_id, poll.message_id)
    message = (
        f"Poll: <b>{html.escape(poll.question)}</b>\n"
        f"Result: <b>{html.escape(option_text)}</b> ({winning_votes} votes)\n\n"
        f"{poll_link}"
    )
    try:
        await context.bot.send_message(
            chat_id=context.settings.decisions_chat_id,
            message_thread_id=context.settings.decisions_topic_id,
            text=message,
            disable_web_page_preview=True,
        )
    except Exception:
        pass


async def _resolve_option_text(
    service: PollsService,
    poll_id: str,
    poll_result: TelegramPoll | None,
    option_id: int,
) -> str | None:
    stored_options = await service.list_poll_options(poll_id)
    for stored_id, text in stored_options:
        if stored_id == option_id:
            return text
    if poll_result is not None and 0 <= option_id < len(poll_result.options):
        return poll_result.options[option_id].text
    return None


def _count_votes_from_poll_result(
    poll_result: TelegramPoll | None,
    ignored_option_ids: set[int],
) -> dict[int, int]:
    if poll_result is None:
        return {}
    counts: dict[int, int] = {}
    for option_id, option in enumerate(poll_result.options):
        if option_id in ignored_option_ids:
            continue
        if option.voter_count > 0:
            counts[option_id] = option.voter_count
    return counts


def _build_poll_link(chat_id: int, message_id: int) -> str:
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        link_id = chat_id_str[4:]
    else:
        link_id = chat_id_str.lstrip("-")
    return f"https://t.me/c/{link_id}/{message_id}"
