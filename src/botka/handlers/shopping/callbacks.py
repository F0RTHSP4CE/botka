from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.handlers.user_links import format_user_link
from botka.services.shopping_list_service import (
    ShoppingBuyConfirmationTracker,
    ShoppingListService,
)
from botka.services.shopping_needs_publisher import ShoppingNeedsPublisher

router = Router(name=__name__)


@router.callback_query(F.data.startswith("buy:"))
@inject
async def buy_callback(
    callback: CallbackQuery,
    settings: FromDishka[Settings],
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
    confirmation_tracker: FromDishka[ShoppingBuyConfirmationTracker],
    user_record: User | None = None,
) -> None:
    if callback.message is None:
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Unknown user.", show_alert=True)
        return
    tier = user_record.tier if user_record else UserTier.guest
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
    if not confirmation_tracker.check_and_clear(item_id, callback.from_user.id):
        confirmation_tracker.set_pending(item_id, callback.from_user.id)
        await callback.answer(
            "Tap again to confirm marking this item as bought.",
        )
        return
    item = await shopping_service.mark_bought(item_id)
    if item is None:
        await callback.answer("Item not found.", show_alert=True)
        return
    view = await needs_publisher.load(shopping_service)
    await needs_publisher.publish(callback.bot, view)
    is_canonical = await needs_publisher.is_canonical_message(
        callback.message.chat.id,
        callback.message.message_thread_id,
        callback.message.message_id,
    )
    if not is_canonical:
        try:
            await callback.message.edit_text(
                view.text,
                parse_mode="HTML",
                reply_markup=view.keyboard,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
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
