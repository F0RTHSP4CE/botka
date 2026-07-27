from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.handlers.user_links import format_user_link
from botka.services.shopping_list_service import ShoppingListService
from botka.services.shopping_needs_publisher import ShoppingNeedsPublisher

router = Router(name=__name__)


@router.message(F.text & ~F.text.startswith("/"))
@inject
async def topic_list_handler(
    message: Message,
    settings: FromDishka[Settings],
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
    user_record: User | None = None,
) -> None:
    if message.text is None:
        return
    if message.from_user is None:
        return
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can manage the shopping list.")
        return
    items = shopping_service.extract_dash_items(message.text)
    if not items:
        return
    await shopping_service.add_items(message.from_user.id, items)
    await needs_publisher.refresh_safely(message.bot, shopping_service)
    if settings.shopping_chat_id is not None:
        actor = format_user_link(message.from_user)
        lines = "\n".join(f"- {html.escape(item)}" for item in items)
        await message.bot.send_message(
            chat_id=settings.shopping_chat_id,
            message_thread_id=settings.shopping_topic_id,
            text=f"🛒 Added by {actor}:\n{lines}",
            disable_web_page_preview=True,
        )
