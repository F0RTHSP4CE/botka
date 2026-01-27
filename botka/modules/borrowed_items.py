"""Borrowed items module for tracking items taken from the space."""

import json
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters
from sqlalchemy import select, and_

from ..db import BorrowedItem, TgUser, get_session

logger = logging.getLogger(__name__)


def is_borrowed_items_chat(chat_id: int, thread_id: Optional[int]) -> bool:
    """Check if message is in borrowed items chat."""
    from ..bot import state

    for chat_config in state.config.telegram.chats.borrowed_items:
        if chat_config.chat == chat_id and chat_config.thread == thread_id:
            return True
    return False


async def handle_borrowed_items_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle messages in borrowed items chat."""
    message = update.message

    if not is_borrowed_items_chat(message.chat_id, message.message_thread_id):
        return

    # Get text from message or caption
    text = message.text or message.caption
    if not text:
        return

    user = message.from_user

    # Parse items from message
    items = parse_items_from_text(text)
    if not items:
        return

    # Create borrowed items record
    items_data = [{"name": item, "returned": None} for item in items]

    async with await get_session() as session:
        # Save user
        result = await session.execute(select(TgUser).where(TgUser.id == user.id))
        tg_user = result.scalar_one_or_none()

        if not tg_user:
            tg_user = TgUser(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            session.add(tg_user)

    # Send confirmation message with return buttons
    keyboard = create_return_keyboard(items_data, message.chat_id, message.message_id)

    reply_text = f"📦 <b>Borrowed items by {user.first_name}:</b>\n\n"
    for i, item in enumerate(items, 1):
        reply_text += f"{i}. {item}\n"
    reply_text += "\nClick to mark as returned:"

    reply_msg = await message.reply_text(
        reply_text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Save to database
    async with await get_session() as session:
        borrowed = BorrowedItem(
            chat_id=message.chat_id,
            user_message_id=message.message_id,
            thread_id=message.message_thread_id or 0,
            bot_message_id=reply_msg.message_id,
            user_id=user.id,
            items=json.dumps(items_data),
            created_at=datetime.utcnow(),
        )
        session.add(borrowed)
        await session.commit()


def parse_items_from_text(text: str) -> list[str]:
    """Parse items from message text."""
    items = []

    for line in text.split("\n"):
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Check for list-like formats
        if line.startswith("-") or line.startswith("•") or line.startswith("*"):
            item = line.lstrip("-•* ").strip()
            if item:
                items.append(item)
        elif line[0].isdigit() and ("." in line[:3] or ")" in line[:3]):
            # Numbered list
            parts = line.split(".", 1) if "." in line[:3] else line.split(")", 1)
            if len(parts) > 1:
                item = parts[1].strip()
                if item:
                    items.append(item)
        elif len(items) == 0 and len(line) > 2:
            # First non-list line could be single item
            items.append(line)

    return items


def create_return_keyboard(
    items: list[dict], chat_id: int, user_msg_id: int
) -> InlineKeyboardMarkup:
    """Create keyboard with return buttons for items."""
    buttons = []

    for i, item in enumerate(items):
        if item.get("returned"):
            # Already returned
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"✅ {item['name'][:30]}",
                        callback_data=f"borrowed:info:{chat_id}:{user_msg_id}:{i}",
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"↩️ {item['name'][:30]}",
                        callback_data=f"borrowed:return:{chat_id}:{user_msg_id}:{i}",
                    )
                ]
            )

    return InlineKeyboardMarkup(buttons)


async def borrowed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle borrowed items callbacks."""
    query = update.callback_query

    data = query.data
    if not data.startswith("borrowed:"):
        return

    parts = data.split(":")
    if len(parts) != 5:
        await query.answer("Invalid callback data")
        return

    action = parts[1]
    try:
        chat_id = int(parts[2])
        user_msg_id = int(parts[3])
        item_index = int(parts[4])
    except ValueError:
        await query.answer("Invalid callback data")
        return

    if action == "return":
        async with await get_session() as session:
            result = await session.execute(
                select(BorrowedItem).where(
                    and_(
                        BorrowedItem.chat_id == chat_id,
                        BorrowedItem.user_message_id == user_msg_id,
                    )
                )
            )
            borrowed = result.scalar_one_or_none()

            if not borrowed:
                await query.answer("Record not found")
                return

            items = json.loads(borrowed.items)

            if item_index >= len(items):
                await query.answer("Item not found")
                return

            if items[item_index].get("returned"):
                await query.answer("Already returned!")
                return

            items[item_index]["returned"] = datetime.utcnow().isoformat()
            borrowed.items = json.dumps(items)
            await session.commit()

            # Get user info
            result = await session.execute(
                select(TgUser).where(TgUser.id == borrowed.user_id)
            )
            tg_user = result.scalar_one_or_none()
            user_name = tg_user.first_name if tg_user else "Unknown"

        # Update message
        reply_text = f"📦 <b>Borrowed items by {user_name}:</b>\n\n"
        all_returned = True
        for i, item in enumerate(items, 1):
            if item.get("returned"):
                reply_text += f"✅ <s>{item['name']}</s>\n"
            else:
                reply_text += f"{i}. {item['name']}\n"
                all_returned = False

        if all_returned:
            reply_text += "\n✅ All items returned!"
        else:
            reply_text += "\nClick to mark as returned:"

        keyboard = create_return_keyboard(items, chat_id, user_msg_id)

        await query.edit_message_text(
            reply_text,
            parse_mode="HTML",
            reply_markup=keyboard if not all_returned else None,
        )

        await query.answer(f"Marked as returned: {items[item_index]['name']}")

    elif action == "info":
        await query.answer("This item was already returned")


def get_message_handler():
    """Get handler for borrowed items messages."""
    return MessageHandler(filters.TEXT | filters.CAPTION, handle_borrowed_items_message)


def get_callback_handler():
    """Get callback handler for borrowed items."""
    return CallbackQueryHandler(borrowed_callback, pattern=r"^borrowed:")
