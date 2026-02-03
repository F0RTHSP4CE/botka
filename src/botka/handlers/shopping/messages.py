from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.handlers.shopping.needs import pin_latest_needs
from botka.handlers.user_links import format_user_link
from botka.services.shopping_list_service import ShoppingListService

router = Router(name=__name__)


@router.message(F.text)
@inject
async def topic_list_handler(
    message: Message,
    settings: FromDishka[Settings],
    shopping_service: FromDishka[ShoppingListService],
) -> None:
    if message.text is None:
        return
    if settings.shopping_topic_id is None:
        return
    if message.message_thread_id != settings.shopping_topic_id:
        return
    if message.from_user is None:
        return
    items = shopping_service.extract_dash_items(message.text)
    if not items:
        return
    await shopping_service.add_items(message.from_user.id, items)
    items_open = await shopping_service.list_open_items()
    await pin_latest_needs(
        message.bot,
        settings.shopping_chat_id,
        settings.shopping_topic_id,
        items_open,
        shopping_service,
        pin=False,
    )
    if settings.shopping_chat_id is not None:
        actor = format_user_link(message.from_user)
        lines = "\n".join(f"- {html.escape(item)}" for item in items)
        await message.bot.send_message(
            chat_id=settings.shopping_chat_id,
            message_thread_id=settings.shopping_topic_id,
            text=f"🛒 Added by {actor}:\n{lines}",
            disable_web_page_preview=True,
        )
