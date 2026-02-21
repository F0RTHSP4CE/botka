"""Format Planka action events as Telegram notification messages."""

from __future__ import annotations

import html

from botka.services.planka_client import PlankaActionEvent, PlankaUser


def notification_text(
    action: PlankaActionEvent,
    board_name: str,
    base_url: str,
    users: list[PlankaUser],
) -> str | None:
    """Return HTML-formatted notification text, or None if the action is not notifiable."""
    card_url = f"{base_url.rstrip('/')}/cards/{action.card_id}" if action.card_id else base_url
    link = f'<b><a href="{html.escape(card_url)}">{html.escape(action.card_name)}</a></b>'
    author = _resolve_author(action.user_id, users)

    if action.type == "createCard" and action.to_list:
        return (
            f"Card Created\n\n"
            f"{author} created {link} "
            f"in <b>{html.escape(action.to_list.name)}</b> on {html.escape(board_name)}"
        )
    if action.type == "moveCard" and action.to_list:
        to_name = (
            "Trash"
            if action.to_list.type == "trash" or action.to_list.name == "?"
            else action.to_list.name
        )
        from_name = action.from_list.name if action.from_list else "?"
        return (
            f"Card Moved\n\n"
            f"{author} moved {link} "
            f"from {html.escape(from_name)} to <b>{html.escape(to_name)}</b> on {html.escape(board_name)}"
        )
    return None


def _resolve_author(user_id: str | None, users: list[PlankaUser]) -> str:
    if not user_id:
        return "Unknown"
    for u in users:
        if u.id == user_id:
            return u.name
    return "Unknown"
