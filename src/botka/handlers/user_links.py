from __future__ import annotations

from html import escape as html_escape

from aiogram.types import User


def format_telegram_username_link(username: str) -> str:
    clean_username = username.removeprefix("@")
    href = f"https://t.me/{clean_username}"
    display = f"@{clean_username}"
    return f'<a href="{html_escape(href, quote=True)}">{html_escape(display)}</a>'


def format_user_link(
    user: User | None = None,
    *,
    telegram_id: int | None = None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> str:
    if user is not None:
        telegram_id = user.id
        username = user.username
        first_name = user.first_name
        last_name = user.last_name
    if username:
        return format_telegram_username_link(username)
    if telegram_id is None:
        return "Unknown"
    name = " ".join(part for part in [first_name, last_name] if part)
    display = name or str(telegram_id)
    href = f"tg://user?id={telegram_id}"
    return (
        f'<a href="{html_escape(href, quote=True)}">' f"{html_escape(display)}" "</a>"
    )
