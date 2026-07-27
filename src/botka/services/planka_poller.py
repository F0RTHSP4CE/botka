"""Background poller for Planka board actions; sends notifications to Telegram."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot

from botka.config import Settings
from botka.handlers.planka.notifications import notification_text
from botka.handlers.user_links import format_telegram_username_link
from botka.services.planka_client import (
    PlankaActionEvent,
    PlankaClient,
    PlankaClientError,
    PlankaUser,
)
from botka.services.planka_notification_service import PlankaNotificationService

logger = logging.getLogger(__name__)

_RELEVANT_TYPES = frozenset({"createCard", "moveCard"})
_DESCRIPTION_SEPARATOR = "\n\n---\n"


def _extract_telegram_username(description: str) -> str | None:
    """Extract the last @username from botka metadata appended to the card description."""
    if _DESCRIPTION_SEPARATOR not in description:
        return None
    _, meta = description.split(_DESCRIPTION_SEPARATOR, 1)
    for line in reversed(meta.splitlines()):
        for part in line.split():
            if part.startswith("@"):
                return part
    return None


def _format_planka_author(
    user_id: str | None, users: list[PlankaUser]
) -> tuple[str, bool]:
    if not user_id:
        return "Unknown", False
    for u in users:
        if u.id == user_id:
            return html.escape(u.username or u.name), False
    return "Unknown", False


async def _resolve_author(
    action: PlankaActionEvent,
    users: list[PlankaUser],
    planka: PlankaClient,
) -> tuple[str, bool]:
    """Return the human-readable author for an action.

    When the acting Planka user is the bot itself, the card description is fetched
    to recover the Telegram username of whoever triggered the command.
    """
    if action.user_id and action.user_id == planka.own_user_id and action.card_id:
        try:
            detail = await planka.get_card(action.card_id)
            if detail:
                tg_user = _extract_telegram_username(detail.description)
                if tg_user:
                    return format_telegram_username_link(tg_user), True
        except Exception:
            logger.debug("Could not fetch card description for action %s", action.id)
    return _format_planka_author(action.user_id, users)


async def run_planka_poller(
    bot: Bot,
    planka: PlankaClient,
    settings: Settings,
    notifications: PlankaNotificationService,
    todo_refresh: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Poll Planka board actions and send notifications to Telegram."""
    board_id = settings.planka_board_id
    if not notifications.has_targets or not board_id:
        logger.info(
            "Planka poller disabled: BOTKA_PLANKA_NOTIFICATION_CHAT_IDS or BOTKA_PLANKA_BOARD_ID not set"
        )
        return
    if not planka.is_ready:
        logger.warning("Planka poller disabled: client did not start successfully")
        return

    base_url = str(settings.planka_base_url)
    interval = settings.planka_poll_interval_seconds
    last_seen_id: str | None = None
    logger.info(
        "Planka poller started, notifying %d target(s)",
        notifications.target_count,
    )

    while True:
        try:
            page = await planka.get_board_actions(board_id)

            new_actions = []
            for action in page.actions:
                if last_seen_id is None:
                    break  # first run: initialise cursor below, don't process
                if not _action_newer(action.id, last_seen_id):
                    break
                new_actions.append(action)

            if page.actions:
                last_seen_id = page.actions[0].id  # keep cursor at newest

            for action in reversed(new_actions):  # oldest-first
                if action.type not in _RELEVANT_TYPES:
                    continue
                author_html, silent = await _resolve_author(action, page.users, planka)
                text = notification_text(
                    action,
                    base_url,
                    author_html,
                    notify_new_quests=settings.planka_notify_new_quests,
                    show_card_links=settings.planka_show_card_links,
                )
                if not text:
                    continue
                await notifications.notify_polled_action(
                    bot,
                    action,
                    text,
                    silent=silent,
                )
            if todo_refresh is not None and new_actions:
                try:
                    await todo_refresh()
                except Exception:
                    logger.exception("Failed to refresh canonical todo messages")

        except PlankaClientError as exc:
            logger.warning("Planka poller error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Planka poller unexpected error")

        await asyncio.sleep(interval)


def _action_newer(aid: str, last_id: str) -> bool:
    """Planka IDs are snowflake-like; larger = newer."""
    try:
        return int(aid) > int(last_id)
    except (ValueError, TypeError):
        return aid != last_id
