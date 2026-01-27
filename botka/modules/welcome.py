"""Welcome module for sending welcome messages to new residents."""

import logging
import re
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select
from datetime import datetime, timedelta

from ..db import Resident, get_session
from ..services import get_wikijs_page

logger = logging.getLogger(__name__)

# Cache of welcomed users to avoid duplicate messages
_welcomed_users: set[int] = set()


def extract_message(text: str) -> Optional[str]:
    """Extract text between > BEGIN and > END markers."""
    begin_tag = "\n> BEGIN\n"
    end_tag = "\n> END\n"

    # Try to find begin marker
    if text.startswith("> BEGIN\n"):
        start_idx = len("> BEGIN\n")
    else:
        idx = text.find(begin_tag)
        if idx == -1:
            return None
        start_idx = idx + len(begin_tag)

    # Find end marker
    remaining = text[start_idx:]
    if remaining.endswith("> END"):
        end_idx = len(remaining) - len("> END")
    else:
        idx = remaining.rfind(end_tag)
        if idx == -1:
            return None
        end_idx = idx

    return remaining[:end_idx].strip()


async def handle_new_members(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle new chat members."""
    from ..bot import state

    message = update.message
    new_members = message.new_chat_members

    if not new_members:
        return

    # Check if this is the primary residential chat
    residential_chats = state.config.telegram.chats.residential
    if not residential_chats or message.chat_id != residential_chats[0]:
        return

    # Filter new residents (joined within last hour)
    newcomers = []

    async with await get_session() as session:
        for member in new_members:
            if member.id in _welcomed_users:
                continue

            if member.is_bot:
                continue

            # Check if they're a new resident (joined within last hour)
            result = await session.execute(
                select(Resident).where(
                    Resident.tg_id == member.id,
                    Resident.end_date.is_(None),
                    Resident.begin_date > datetime.utcnow() - timedelta(hours=1),
                )
            )
            if result.scalar_one_or_none():
                newcomers.append(member)

    if not newcomers:
        return

    # Get welcome message from Wiki.js
    if not state.config.services.wikijs:
        logger.warning("Wiki.js not configured, skipping welcome message")
        return

    try:
        page_content = await get_wikijs_page(
            state.http_client,
            state.config.services.wikijs,
        )

        if not page_content:
            logger.error("Failed to fetch welcome page")
            return

        template = extract_message(page_content)
        if not template:
            logger.error("No BEGIN/END markers in welcome page")
            return
    except Exception as e:
        logger.error(f"Failed to get welcome message: {e}")
        return

    # Format newcomers
    newcomer_links = ", ".join(
        f'<a href="tg://user?id={m.id}">{m.first_name}</a>' for m in newcomers
    )

    text = template.replace("%newcomer%", newcomer_links)

    # Create edit button
    wikijs = state.config.services.wikijs
    edit_url = f"{wikijs.url}/{wikijs.welcome_message_page.lstrip('/')}"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✏️ Edit this message", url=edit_url)]]
    )

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

    # Mark as welcomed
    for member in newcomers:
        _welcomed_users.add(member.id)


def get_message_handler():
    """Get handler for new member messages."""
    return MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members)
