from __future__ import annotations

import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject
from botka.handlers.menu import Btn
from botka.handlers.user_links import format_user_link
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.user_service import UserService

router = Router(name=__name__)


async def _do_borrowed(
    message: Message,
    user_service: UserService,
    borrowed_service: BorrowedItemsService,
) -> None:
    items = await borrowed_service.list_open_items()
    if not items:
        await message.reply("No borrowed items.")
        return
    user_ids = {item.created_by_telegram_id for item in items}
    users = await user_service.list_users_by_telegram_ids(user_ids)
    users_map = {user.telegram_id: user for user in users}
    lines = []
    for item in items:
        approx_time = _format_borrowed_age(item.created_at)
        message_link = _build_message_link(item.chat_id, item.message_id)
        user = users_map.get(item.created_by_telegram_id)
        user_link = format_user_link(
            telegram_id=item.created_by_telegram_id,
            username=user.username if user else None,
        )
        item_text = html.escape(item.item_name)
        if message_link:
            item_part = f'<a href="{message_link}">{item_text}</a>'
        else:
            item_part = f"{item_text}"
        lines.append(f"- {item_part} {approx_time} {user_link}")
    await message.reply(
        "Borrowed items:\n" + "\n".join(lines),
        disable_web_page_preview=True,
    )


@router.message(Command("borrowed"))
@inject
async def borrowed_list_handler(
    message: Message,
    user_service: FromDishka[UserService],
    borrowed_service: FromDishka[BorrowedItemsService],
) -> None:
    if message.from_user is None:
        await message.reply("Unknown user.")
        return
    await _do_borrowed(message, user_service, borrowed_service)


@router.message(F.text == Btn.BORROWED, F.chat.type == "private")
@inject
async def menu_borrowed_message(
    message: Message,
    user_service: FromDishka[UserService],
    borrowed_service: FromDishka[BorrowedItemsService],
) -> None:
    await _do_borrowed(message, user_service, borrowed_service)


def _format_borrowed_age(created_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    created = created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    delta_days = max((now - created).days, 0)
    if delta_days >= 30:
        months = max(round(delta_days / 30), 1)
        label = "month" if months == 1 else "months"
        return f"~{months} {label}"
    if delta_days >= 1:
        label = "day" if delta_days == 1 else "days"
        return f"~{delta_days} {label}"
    return "today"


def _build_message_link(chat_id: int, message_id: int) -> str | None:
    chat_id_str = str(chat_id)
    if not chat_id_str.startswith("-100"):
        return None
    internal_id = chat_id_str.removeprefix("-100")
    return f"https://t.me/c/{internal_id}/{message_id}"
