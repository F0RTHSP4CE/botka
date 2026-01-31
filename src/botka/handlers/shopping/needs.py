from __future__ import annotations

from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from botka.db.models import ShoppingItem
from botka.services.shopping_list_service import ShoppingListService


def build_needs_keyboard(items: Sequence[ShoppingItem]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=item.text, callback_data=f"buy:{item.id}")]
        for item in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def needs_text(items: Sequence[ShoppingItem]) -> str:
    if not items:
        return "Shopping list is empty."
    return "Shopping list:"


async def _unpin_topic_messages(bot: Bot, chat_id: int, topic_id: int) -> None:
    await bot.unpin_all_forum_topic_messages(
        chat_id=chat_id,
        message_thread_id=topic_id,
    )


async def pin_latest_needs(
    bot: Bot,
    chat_id: int | None,
    topic_id: int | None,
    items: Sequence[ShoppingItem],
    shopping_service: ShoppingListService,
    message: Message | None = None,
    *,
    pin: bool = True,
) -> Message | None:
    if chat_id is None or topic_id is None:
        return None
    text = needs_text(items)
    reply_markup = build_needs_keyboard(items) if items else None
    target = message
    if (
        target is None
        or target.chat.id != chat_id
        or target.message_thread_id != topic_id
    ):
        cached_id = await shopping_service.get_needs_message_id(chat_id, topic_id)
        if cached_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=cached_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                return None
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return None
                await shopping_service.clear_needs_message_id(chat_id, topic_id)
        if not pin:
            return None
        await _unpin_topic_messages(bot, chat_id, topic_id)
        target = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        try:
            await target.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    if target is None:
        return None
    await shopping_service.set_needs_message_id(
        chat_id,
        topic_id,
        target.message_id,
    )
    if pin:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=target.message_id,
            disable_notification=True,
        )
    return target
