from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple


from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from botka.db.models import Poll, PollAudience, User
from botka.handlers.user_links import format_user_link
from botka.services.polls_service import PollsService
from botka.services.user_service import UserService


class ParsedPoll(NamedTuple):
    audience: PollAudience
    question: str
    display_question: str


_POLL_IGNORED_OPTION_IDS: dict[str, set[int]] = {}
_IGNORED_POLL_OPTION_KEYS = ("abstain", "see results")


_POLL_PREFIX_RE = re.compile(
    r"^\[(residents|members|everyone)\]\s*(.+)$", re.IGNORECASE
)


def parse_poll_question(raw_question: str) -> ParsedPoll | None:
    stripped = raw_question.lstrip()
    if not stripped.startswith("!"):
        return None
    without_bang = stripped[1:].strip()
    if not without_bang:
        return None
    match = _POLL_PREFIX_RE.match(without_bang)
    if match:
        audience_tag = match.group(1).lower()
        audience = PollAudience(audience_tag)
        question = match.group(2).strip()
        display_question = f"[{audience_tag}] {question}" if question else ""
    else:
        audience = PollAudience.everyone
        question = without_bang
        display_question = question
    if not question:
        return None
    return ParsedPoll(
        audience=audience,
        question=question,
        display_question=display_question,
    )


def _normalize_option_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.casefold().strip())
    return re.sub(r"[^a-z0-9 ]+", "", normalized).strip()


def _is_ignored_option_text(text: str) -> bool:
    normalized = _normalize_option_text(text)
    if not normalized:
        return False
    for key in _IGNORED_POLL_OPTION_KEYS:
        if normalized == key or normalized.startswith(f"{key} "):
            return True
    return False


def register_poll_ignored_options(poll_id: str, option_texts: list[str]) -> set[int]:
    ignored = set()
    for index, text in enumerate(option_texts):
        if _is_ignored_option_text(text):
            ignored.add(index)
    if ignored:
        _POLL_IGNORED_OPTION_IDS[poll_id] = ignored
    else:
        _POLL_IGNORED_OPTION_IDS.pop(poll_id, None)
    return ignored


def get_poll_ignored_option_ids(poll_id: str) -> set[int]:
    return _POLL_IGNORED_OPTION_IDS.get(poll_id, set())


def cache_poll_ignored_option_ids(poll_id: str, option_ids: set[int]) -> None:
    if option_ids:
        _POLL_IGNORED_OPTION_IDS[poll_id] = set(option_ids)
    else:
        _POLL_IGNORED_OPTION_IDS.pop(poll_id, None)


async def refresh_awaiting_message(
    bot: Bot,
    poll: Poll,
    polls_service: PollsService,
    user_service: UserService,
) -> None:
    if poll.awaiting_message_id is None or poll.closed:
        return
    target_users = list(await polls_service.list_target_users(poll.audience))
    voted_ids = await polls_service.list_voted_user_ids(poll.poll_id)
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


def build_awaiting_text(
    users: list[User],
    closes_at: datetime,
    warning_users: list[User] | None = None,
) -> str:
    closes_label = format_close_time(closes_at)
    remaining_label = format_remaining_time(closes_at)
    if not users:
        awaiting_line = "<b>Everyone has voted.</b>"
    else:
        mentions = ", ".join(
            format_user_link(telegram_id=user.telegram_id, username=user.username)
            for user in users
        )
        awaiting_line = f"Awaiting vote from {mentions}"

    lines = [
        f"Closes in <b>{remaining_label}</b> ({closes_label})",
        awaiting_line,
    ]

    if warning_users:
        warning_mentions = ", ".join(
            format_user_link(telegram_id=user.telegram_id, username=user.username)
            for user in warning_users
        )
        lines.extend(
            [
                "",
                f"⚠️❌ Votes from users the poll was NOT intended for: <b>{warning_mentions}</b>",
            ]
        )

    message = "\n".join(lines)
    return message


def format_close_time(closes_at: datetime) -> str:
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    return closes_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_remaining_time(closes_at: datetime, now: datetime | None = None) -> str:
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = closes_at - now
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


def poll_close_at(now: datetime | None = None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    return now + timedelta(days=7)
