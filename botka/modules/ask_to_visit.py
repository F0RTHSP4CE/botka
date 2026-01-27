"""Ask to visit module - forwards messages from non-residents to active residents."""

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from sqlalchemy import select, and_

from ..db import Resident, get_session

logger = logging.getLogger(__name__)


async def handle_ask_to_visit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle messages in ask-to-visit chat."""
    from ..bot import state, is_resident

    message = update.message

    # Check if this is the ask_to_visit chat
    ask_to_visit = state.config.telegram.chats.ask_to_visit
    if not ask_to_visit:
        return

    if message.chat_id != ask_to_visit.chat:
        return

    if message.message_thread_id != ask_to_visit.thread:
        return

    text = message.text
    if not text:
        return

    # Skip comments (starting with //)
    if text.startswith("//"):
        return

    user = message.from_user
    if not user:
        return

    # Check if sender is a resident - if so, don't forward
    if await is_resident(user.id):
        return

    # Get active (online) residents
    active_resident_ids = state.active_users
    if not active_resident_ids:
        return

    # Get resident user IDs from database
    async with await get_session() as session:
        result = await session.execute(
            select(Resident.tg_id).where(
                and_(
                    Resident.end_date.is_(None), Resident.tg_id.in_(active_resident_ids)
                )
            )
        )
        resident_ids = [r[0] for r in result.all()]

    logger.debug(
        f"Forwarding ask_to_visit message to {len(resident_ids)} active residents"
    )

    # Forward message to each active resident
    for resident_id in resident_ids:
        try:
            await context.bot.forward_message(
                chat_id=resident_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as e:
            logger.error(f"Failed to forward message to resident {resident_id}: {e}")


def get_message_handler():
    """Get handler for ask-to-visit messages."""
    return MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ask_to_visit)
