"""Shopping list (needs) module."""

import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from sqlalchemy import select, and_

from ..db import NeededItem, TgUser, get_session

logger = logging.getLogger(__name__)


async def cmd_needs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show shopping list."""
    from ..bot import is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    async with await get_session() as session:
        result = await session.execute(
            select(NeededItem, TgUser)
            .outerjoin(TgUser, NeededItem.request_user_id == TgUser.id)
            .where(NeededItem.buyer_user_id.is_(None))
            .order_by(NeededItem.rowid)
        )
        rows = result.all()

    if not rows:
        await update.message.reply_text("No items needed. 🎉")
        return

    text = "<b>Shopping list:</b>\n\n"
    buttons = []

    for i, (item, tg_user) in enumerate(rows, 1):
        user_name = tg_user.first_name if tg_user else "Unknown"
        text += f"{i}. {item.item} (by {user_name})\n"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ {i}. {item.item[:20]}...",
                    callback_data=f"needs:buy:{item.rowid}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def cmd_need(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add item to shopping list."""
    from ..bot import is_resident, get_or_create_user

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /need <item>")
        return

    item_text = " ".join(context.args)

    async with await get_session() as session:
        await get_or_create_user(session, user)

        new_item = NeededItem(
            request_chat_id=update.message.chat_id,
            request_message_id=update.message.message_id,
            request_user_id=user.id,
            pinned_chat_id=update.message.chat_id,
            pinned_message_id=update.message.message_id,
            item=item_text,
        )
        session.add(new_item)
        await session.commit()

    await update.message.reply_text(f"✅ Added to shopping list: {item_text}")


async def needs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle needs callback buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("needs:"):
        return

    parts = data.split(":")
    if len(parts) != 3 or parts[1] != "buy":
        return

    try:
        item_id = int(parts[2])
    except ValueError:
        return

    user = update.effective_user

    async with await get_session() as session:
        result = await session.execute(
            select(NeededItem).where(NeededItem.rowid == item_id)
        )
        item = result.scalar_one_or_none()

        if not item:
            await query.edit_message_text("Item not found.")
            return

        if item.buyer_user_id is not None:
            await query.answer("This item was already bought!", show_alert=True)
            return

        item.buyer_user_id = user.id
        await session.commit()

    await query.answer(f"Marked as bought: {item.item}")

    # Refresh the list
    async with await get_session() as session:
        result = await session.execute(
            select(NeededItem, TgUser)
            .outerjoin(TgUser, NeededItem.request_user_id == TgUser.id)
            .where(NeededItem.buyer_user_id.is_(None))
            .order_by(NeededItem.rowid)
        )
        rows = result.all()

    if not rows:
        await query.edit_message_text("No items needed. 🎉", parse_mode="HTML")
        return

    text = "<b>Shopping list:</b>\n\n"
    buttons = []

    for i, (item, tg_user) in enumerate(rows, 1):
        user_name = tg_user.first_name if tg_user else "Unknown"
        text += f"{i}. {item.item} (by {user_name})\n"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ {i}. {item.item[:20]}...",
                    callback_data=f"needs:buy:{item.rowid}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


def get_callback_handler():
    """Get callback handler for needs module."""
    return CallbackQueryHandler(needs_callback, pattern=r"^needs:")
