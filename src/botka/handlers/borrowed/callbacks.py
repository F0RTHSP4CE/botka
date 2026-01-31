from __future__ import annotations

import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.borrowed.utils import build_return_keyboard
from botka.handlers.user_links import format_user_link
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.callback_query(F.data.startswith("borrowed_return:"))
@inject
async def borrowed_return_callback(
    callback: CallbackQuery,
    borrowed_service: FromDishka[BorrowedItemsService],
    user_service: FromDishka[UserService],
) -> None:
    if callback.message is None:
        await callback.answer("No message context.", show_alert=True)
        return
    _, raw_id = callback.data.split(":", 1)
    try:
        item_id = int(raw_id)
    except ValueError:
        await callback.answer("Invalid item.", show_alert=True)
        return
    current = await borrowed_service.get_item(item_id)
    if current is None:
        await callback.answer("Item not found.", show_alert=True)
        return
    was_already_returned = current.returned
    item = await borrowed_service.mark_returned(item_id)
    if item is None:
        await callback.answer("Item not found.", show_alert=True)
        return
    all_items = await borrowed_service.list_items_for_message(
        item.chat_id,
        item.message_id,
    )
    remaining = [entry for entry in all_items if not entry.returned]
    user_ids = {entry.created_by_telegram_id for entry in all_items}
    users = await user_service.list_users_by_telegram_ids(user_ids)
    users_map = {user.telegram_id: user for user in users}
    lines = []
    for entry in all_items:
        user = users_map.get(entry.created_by_telegram_id)
        user_link = format_user_link(
            telegram_id=entry.created_by_telegram_id,
            username=user.username if user else None,
        )
        returned_part = (
            f" · returned {_format_date(entry.returned_at)}"
            if entry.returned_at
            else ""
        )
        item_text = html.escape(entry.item_name)
        lines.append(f" • <b>{item_text}</b> — {user_link} {returned_part}")
    header = "✅ Returned items:" if not remaining else "📌 Borrowed items:"
    try:
        await callback.message.edit_text(
            header + "\n" + "\n".join(lines),
            reply_markup=build_return_keyboard(
                [(entry.id, entry.item_name, entry.returned) for entry in all_items]
            ),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        message = str(exc)
        if "message is not modified" in message:
            if was_already_returned:
                await callback.answer("Already returned.")
                return
            await callback.answer("No changes.")
            return
        if not was_already_returned:
            await borrowed_service.mark_unreturned(item_id)
        await callback.answer("Failed to update message.", show_alert=True)
        return
    if not remaining:
        try:
            await callback.bot.unpin_chat_message(
                chat_id=item.chat_id,
                message_id=item.message_id,
            )
        except Exception:
            pass
    await callback.answer("Marked as returned.")


def _format_date(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
