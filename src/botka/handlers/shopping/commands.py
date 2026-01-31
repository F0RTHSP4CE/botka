from __future__ import annotations

import html

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.handlers.user_links import format_user_link
from botka.handlers.shopping.needs import (
    build_needs_keyboard,
    needs_text,
    pin_latest_needs,
)
from botka.services.shopping_list_service import ShoppingListService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.message(Command("need"))
@inject
async def need_handler(
    message: Message,
    command: CommandObject,
    settings: FromDishka[Settings],
    user_service: FromDishka[UserService],
    shopping_service: FromDishka[ShoppingListService],
) -> None:
    if message.from_user is None:
        await message.reply(html.escape("Unknown user."))
        return
    await user_service.ensure_user(message.from_user.id, message.from_user.username)
    text = (command.args or "").strip()
    if not text:
        await message.reply(html.escape("Usage: /need <item>"))
        return
    dash_items = shopping_service.extract_dash_items(text)
    if dash_items:
        await shopping_service.add_items(message.from_user.id, dash_items)
        added_items = dash_items
    else:
        await shopping_service.add_item(message.from_user.id, text)
        added_items = [text]
    items = await shopping_service.list_open_items()
    await pin_latest_needs(
        message.bot,
        settings.shopping_chat_id,
        settings.shopping_topic_id,
        items,
        shopping_service,
        pin=False,
    )
    if settings.shopping_topic_id != message.message_thread_id:
        if len(added_items) == 1:
            await message.reply(
                f"Added <b>{html.escape(added_items[0])}</b> to the shopping list."
            )
        else:
            await message.reply(
                f"Added <b>{len(added_items)}</b> items to the shopping list."
            )
    if settings.shopping_chat_id is not None:
        actor = format_user_link(message.from_user)
        if len(added_items) == 1:
            item_text = html.escape(added_items[0])
            await message.bot.send_message(
                chat_id=settings.shopping_chat_id,
                message_thread_id=settings.shopping_topic_id,
                text=f"🛒 Added <b>{item_text}</b> by {actor}",
                disable_web_page_preview=True,
            )
        else:
            lines = "\n".join(f"- {html.escape(item)}" for item in added_items)
            await message.bot.send_message(
                chat_id=settings.shopping_chat_id,
                message_thread_id=settings.shopping_topic_id,
                text=f"🛒 Added by {actor}:\n{lines}",
                disable_web_page_preview=True,
            )


@router.message(Command("needs"))
@inject
async def needs_handler(
    message: Message,
    settings: FromDishka[Settings],
    user_service: FromDishka[UserService],
    shopping_service: FromDishka[ShoppingListService],
) -> None:
    await user_service.ensure_user(message.from_user.id, message.from_user.username)
    items = await shopping_service.list_open_items()
    is_shopping_thread = (
        settings.shopping_chat_id == message.chat.id
        and settings.shopping_topic_id == message.message_thread_id
    )
    if is_shopping_thread:
        previous_message_id = await shopping_service.get_needs_message_id(
            settings.shopping_chat_id,
            settings.shopping_topic_id,
        )
        response = await message.reply(
            needs_text(items),
            reply_markup=build_needs_keyboard(items) if items else None,
        )
        if (
            previous_message_id is not None
            and previous_message_id != response.message_id
        ):
            try:
                await message.bot.delete_message(
                    chat_id=settings.shopping_chat_id,
                    message_id=previous_message_id,
                )
            except TelegramBadRequest:
                pass
        await pin_latest_needs(
            message.bot,
            settings.shopping_chat_id,
            settings.shopping_topic_id,
            items,
            shopping_service,
            message=response,
            pin=True,
        )
        return
    await message.reply(
        needs_text(items),
        reply_markup=build_needs_keyboard(items) if items else None,
    )
