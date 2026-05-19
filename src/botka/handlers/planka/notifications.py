"""Format Planka action events as Telegram notification messages."""

from __future__ import annotations

import html
import random

from botka.services.planka_client import PlankaActionEvent

_DONE_KEYWORDS = frozenset({"done", "complete", "completed", "finish", "finished"})
_DOING_KEYWORDS = frozenset({"progress", "doing", "active", "working", "taken"})
_TODO_KEYWORDS = frozenset(
    {"todo", "to-do", "to do", "available", "backlog", "open", "quest"}
)

_QUEST_DONE_EMOJIS = [
    "🎉",
    "🌟",
    "🔥",
    "🏆",
    "✨",
    "🎊",
    "⭐",
    "💫",
    "🎯",
    "🥳",
    "🍾",
    "🪄",
    "🏅",
    "💥",
    "🌈",
    "🎖️",
    "🚀",
    "💎",
    "👑",
    "🦾",
    "⚡",
    "🌠",
    "🎆",
    "🎇",
    "🪩",
    "🥂",
    "🍻",
    "🤩",
    "😎",
    "🙌",
    "👏",
    "💪",
    "🫡",
    "🫶",
    "❤️‍🔥",
    "🐉",
    "⚔️",
    "🦄",
    "🍷",
    "🧬",
    "🛡️",
    "🛠️",
]


def _classify_list(name: str, list_type: str) -> str:
    """Return 'trash', 'done', 'doing', 'todo', or 'other' for a Planka list."""
    if list_type == "trash" or name == "?":
        return "trash"
    lower = name.lower()
    if any(kw in lower for kw in _DONE_KEYWORDS):
        return "done"
    if any(kw in lower for kw in _DOING_KEYWORDS):
        return "doing"
    if any(kw in lower for kw in _TODO_KEYWORDS):
        return "todo"
    return "other"


def notification_text(
    action: PlankaActionEvent,
    board_name: str,
    base_url: str,
    author_html: str,
    *,
    notify_new_quests: bool = True,
    show_card_links: bool = True,
) -> str | None:
    """Return HTML-formatted notification text, or None if the action is not notifiable."""
    card_url = (
        f"{base_url.rstrip('/')}/cards/{action.card_id}" if action.card_id else base_url
    )
    escaped_name = html.escape(action.card_name)
    if show_card_links and card_url:
        link = f'<a href="{html.escape(card_url)}">{escaped_name}</a>'
    else:
        link = f"<b>{escaped_name}</b>"

    if action.type == "createCard" and action.to_list:
        if not notify_new_quests:
            return None
        return f"📜 New quest: {link} — added by {author_html}"

    if action.type == "moveCard" and action.to_list:
        kind = _classify_list(action.to_list.name, action.to_list.type)
        if kind == "trash":
            return f"🗑️ {link} — discarded by {author_html}"
        if kind == "done":
            rng = random.Random()
            prefix = "".join(rng.choices(_QUEST_DONE_EMOJIS, k=rng.randint(1, 2)))
            suffix = "".join(rng.choices(_QUEST_DONE_EMOJIS, k=rng.randint(1, 2)))
            return f"{prefix} {author_html} completed the quest {link} {suffix}"
        if kind == "doing":
            return f"⚔️ {link} — taken by {author_html}"
        if kind == "todo":
            return f"🏳️ {link} — quest abandoned by {author_html}"
        list_name = html.escape(action.to_list.name)
        return f"🔄 {link} — moved to <b>{list_name}</b> by {author_html}"

    return None
