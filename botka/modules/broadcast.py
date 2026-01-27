"""Broadcast module for sending messages to all residents."""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select

from ..db import Resident, get_session

logger = logging.getLogger(__name__)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all residents. Use as reply to message."""
    from ..bot import is_admin

    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("This command is only available to admins.")
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "Please reply to the message you want to broadcast with this command."
        )
        return

    # Get all active residents
    async with await get_session() as session:
        result = await session.execute(
            select(Resident.tg_id).where(Resident.end_date.is_(None))
        )
        residents = [r[0] for r in result.all()]

    if not residents:
        await update.message.reply_text("Resident list is empty, broadcast canceled.")
        return

    await update.message.reply_text(
        f"Starting broadcast to {len(residents)} residents…"
    )

    # Broadcast in background
    asyncio.create_task(
        broadcast_message(
            context.bot,
            reply.chat_id,
            reply.message_id,
            residents,
            update.message.chat_id,
        )
    )


async def broadcast_message(
    bot,
    src_chat_id: int,
    src_message_id: int,
    recipients: list[int],
    admin_chat_id: int,
) -> None:
    """Send message to all recipients."""
    sent_ok = 0
    failed = 0

    for user_id in recipients:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=src_chat_id,
                message_id=src_message_id,
            )
            sent_ok += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast: failed to send to {user_id}: {e}")

        # Rate limiting (~25 msgs/sec)
        await asyncio.sleep(0.04)

    summary = f"Broadcast finished.\nSuccessfully sent: {sent_ok}\nFailed: {failed}"
    await bot.send_message(admin_chat_id, summary)
