"""Background poller for Planka board actions; sends notifications to Telegram."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from botka.config import Settings
from botka.handlers.planka.notifications import notification_text
from botka.services.planka_client import PlankaClient, PlankaClientError

logger = logging.getLogger(__name__)

_RELEVANT_TYPES = frozenset({"createCard", "moveCard"})


async def run_planka_poller(bot: Bot, planka: PlankaClient, settings: Settings) -> None:
    """Poll Planka board actions and send notifications to Telegram."""
    targets = settings.get_planka_notification_targets()
    board_id = settings.planka_board_id
    if not targets or not board_id:
        logger.info(
            "Planka poller disabled: BOTKA_PLANKA_NOTIFICATION_CHAT_IDS or BOTKA_PLANKA_BOARD_ID not set"
        )
        return
    if not planka.is_ready:
        logger.warning("Planka poller disabled: client did not start successfully")
        return

    base_url = str(settings.planka_base_url)
    board_name = settings.planka_board_name
    interval = settings.planka_poll_interval_seconds
    last_seen_id: str | None = None
    logger.info("Planka poller started, notifying %d target(s)", len(targets))

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
                text = notification_text(action, board_name, base_url, page.users)
                if not text:
                    continue
                for chat_id, thread_id in targets:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            message_thread_id=thread_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to send notification for action %s to %s",
                            action.id,
                            chat_id,
                        )

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
