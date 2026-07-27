from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.types import User as TelegramUser
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.handlers.menu import Btn, cancel_kb, send_main_menu
from botka.handlers.user_links import format_user_link
from botka.services.shopping_list_service import ShoppingListService
from botka.services.shopping_needs_publisher import ShoppingNeedsPublisher

router = Router(name=__name__)


class NeedDialog(StatesGroup):
    waiting_text = State()


async def _do_need_item(
    message: Message,
    item_text: str,
    settings: Settings,
    shopping_service: ShoppingListService,
    needs_publisher: ShoppingNeedsPublisher,
    user_record: User | None,
    tg_user: TelegramUser,
) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can manage the shopping list.")
        return
    dash_items = shopping_service.extract_dash_items(item_text)
    if dash_items:
        await shopping_service.add_items(tg_user.id, dash_items)
        added_items = dash_items
    else:
        await shopping_service.add_item(tg_user.id, item_text)
        added_items = [item_text]
    await needs_publisher.refresh_safely(message.bot, shopping_service)
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
        actor = format_user_link(tg_user)
        if len(added_items) == 1:
            item_text_esc = html.escape(added_items[0])
            await message.bot.send_message(
                chat_id=settings.shopping_chat_id,
                message_thread_id=settings.shopping_topic_id,
                text=f"🛒 Added <b>{item_text_esc}</b> by {actor}",
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


async def _do_needs(
    message: Message,
    shopping_service: ShoppingListService,
    needs_publisher: ShoppingNeedsPublisher,
) -> None:
    view = await needs_publisher.load(shopping_service)
    if message.chat.type == "private":
        await message.reply(
            view.text,
            parse_mode="HTML",
            reply_markup=view.keyboard,
        )
        return
    if not needs_publisher.has_target:
        await message.reply("Shopping list topic is not configured.")
        return

    publication = await needs_publisher.publish(message.bot, view)
    if publication.link:
        await message.reply(
            f'📌 <a href="{html.escape(publication.link)}">'
            "Open the pinned shopping list</a>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    elif publication.published:
        await message.reply("The pinned shopping list was updated.")
    else:
        await message.reply(
            "I couldn't update the pinned shopping list. Please try again later."
        )


@router.message(Command("need"))
@inject
async def need_handler(
    message: Message,
    command: CommandObject,
    settings: FromDishka[Settings],
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply(html.escape("Unknown user."))
        return
    text = (command.args or "").strip()
    if not text:
        await message.reply(html.escape("Usage: /need <item>"))
        return
    await _do_need_item(
        message,
        text,
        settings,
        shopping_service,
        needs_publisher,
        user_record,
        message.from_user,
    )


@router.message(Command("needs"))
@inject
async def needs_handler(
    message: Message,
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
) -> None:
    await _do_needs(message, shopping_service, needs_publisher)


@router.message(F.text == Btn.NEEDS, F.chat.type == "private")
@inject
async def menu_needs_message(
    message: Message,
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
) -> None:
    await _do_needs(message, shopping_service, needs_publisher)


@router.message(F.text == Btn.NEED_ITEM, F.chat.type == "private")
@inject
async def menu_need_start_message(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(NeedDialog.waiting_text)
    await message.reply(
        "What do you need? Enter one item or a list (one per line, or start each with a dash):",
        reply_markup=cancel_kb(),
    )


@router.message(NeedDialog.waiting_text, F.text != Btn.CANCEL)
@inject
async def need_dialog_text_handler(
    message: Message,
    settings: FromDishka[Settings],
    shopping_service: FromDishka[ShoppingListService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
    state: FSMContext,
    user_record: User | None = None,
) -> None:
    await state.clear()
    if message.from_user is None or not message.text:
        return
    await _do_need_item(
        message,
        message.text.strip(),
        settings,
        shopping_service,
        needs_publisher,
        user_record,
        message.from_user,
    )
    await send_main_menu(message)
