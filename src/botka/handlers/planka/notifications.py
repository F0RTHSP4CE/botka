"""Format Planka action events as Telegram notification messages."""

from __future__ import annotations

import html

from botka.services.planka_client import PlankaActionEvent


def notification_text(
    action: PlankaActionEvent,
    board_name: str,
    base_url: str,
    author_html: str,
) -> str | None:
    """Return HTML-formatted notification text, or None if the action is not notifiable."""
    card_url = f"{base_url.rstrip('/')}/cards/{action.card_id}" if action.card_id else base_url
    link = f'<a href="{html.escape(card_url)}">{html.escape(action.card_name)}</a>'

    if action.type == "createCard" and action.to_list:
        list_name = html.escape(action.to_list.name)
        return f"{link} created in <b>{list_name}</b> by {author_html}"

    if action.type == "moveCard" and action.to_list:
        to_name = (
            "Trash"
            if action.to_list.type == "trash" or action.to_list.name == "?"
            else action.to_list.name
        )
        return f"{link} moved to <b>{html.escape(to_name)}</b> by {author_html}"

    return None
