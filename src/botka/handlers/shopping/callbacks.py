from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.handlers.shopping.needs import build_needs_keyboard, pin_latest_needs
from botka.handlers.user_links import format_user_link
from botka.db.models import UserTier
from botka.services.shopping_list_service import ShoppingListService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.callback_query(F.data.startswith("buy:"))
@inject
async def buy_callback(
    callback: CallbackQuery,
    settings: FromDishka[Settings],
    user_service: FromDishka[UserService],
    shopping_service: FromDishka[ShoppingListService],
) -> None:
    if callback.message is None:
        await callback.answer("No message context.", show_alert=True)
        return
    tier = await user_service.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
    )
    if tier not in (UserTier.resident, UserTier.member):
        await callback.answer(
            "Only residents and members can mark items as bought.",
            show_alert=True,
        )
        return
    _, raw_id = callback.data.split(":", 1)
    try:
        item_id = int(raw_id)
    except ValueError:
        await callback.answer("Invalid item.", show_alert=True)
        return
    item = await shopping_service.mark_bought(item_id)
    if item is None:
        await callback.answer("Item not found.", show_alert=True)
        return
    items = await shopping_service.list_open_items()
    if settings.shopping_chat_id is not None and settings.shopping_topic_id is not None:
        await pin_latest_needs(
            callback.bot,
            settings.shopping_chat_id,
            settings.shopping_topic_id,
            items,
            shopping_service,
            message=callback.message,
            pin=False,
        )
    else:
        if not items:
            await callback.message.edit_text("Shopping list is empty.")
        else:
            await callback.message.edit_text(
                "Shopping list:",
                reply_markup=build_needs_keyboard(items),
            )
    if settings.shopping_chat_id is not None:
        actor = format_user_link(callback.from_user)
        item_text = html.escape(item.text)
        await callback.bot.send_message(
            chat_id=settings.shopping_chat_id,
            message_thread_id=settings.shopping_topic_id,
            text=f"✅ Marked as bought by {actor}: <b>{item_text}</b>",
            disable_web_page_preview=True,
        )
    await callback.answer("Marked as bought.")
