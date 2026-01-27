"""Butler module for door opening functionality."""

import logging
import secrets
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from sqlalchemy import select, and_

from ..db import TempOpenToken, get_session
from ..services import open_door

logger = logging.getLogger(__name__)


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the door."""
    from ..bot import state, is_resident

    user = update.effective_user

    # Check if resident
    if await is_resident(user.id):
        logger.info(f"Resident {user.full_name} ({user.id}) opened the door")
    else:
        # Check if guest with valid token
        async with await get_session() as session:
            now = datetime.utcnow()
            result = await session.execute(
                select(TempOpenToken).where(
                    and_(
                        TempOpenToken.guest_tg_id == user.id,
                        TempOpenToken.expires_at > now,
                        TempOpenToken.used_at.is_(None),
                    )
                )
            )
            token = result.scalar_one_or_none()

            if not token:
                await update.message.reply_text(
                    "Only residents or guests with a valid access link can open the door."
                )
                return

            # Check if inviter is online
            if token.resident_tg_id not in state.active_users:
                await update.message.reply_text(
                    "The resident who invited you is not currently on Wi-Fi. Door cannot be opened."
                )
                return

            logger.info(
                f"Guest {user.full_name} ({user.id}) used temp_open (inviter: {token.resident_tg_id})"
            )

    if not state.config.services.butler:
        await update.message.reply_text("Door opening is not configured.")
        return

    butler = state.config.services.butler
    success = await open_door(state.http_client, butler.url, butler.token)

    if success:
        await update.message.reply_text("🚪 Door opened!")
    else:
        await update.message.reply_text("Failed to open door. Please try again.")


async def cmd_temp_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a temporary guest door access link."""
    from ..bot import state, is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    # Check if user is online
    is_online = user.id in state.active_users

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("5 minutes", callback_data="butler:temp:5"),
                InlineKeyboardButton("15 minutes", callback_data="butler:temp:15"),
            ],
            [
                InlineKeyboardButton("30 minutes", callback_data="butler:temp:30"),
                InlineKeyboardButton("1 hour", callback_data="butler:temp:60"),
            ],
        ]
    )

    text = "🕒 Select duration for temporary guest access:"
    if not is_online:
        text += "\n\n⚠️ You are not detected on the hackerspace Wi-Fi. The link will only work if you are at the space."

    await update.message.reply_text(text, reply_markup=keyboard)


async def butler_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle butler callback buttons."""
    from ..bot import state

    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("butler:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    action = parts[1]

    if action == "temp":
        try:
            minutes = int(parts[2])
        except ValueError:
            return

        user = update.effective_user
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=minutes)

        async with await get_session() as session:
            new_token = TempOpenToken(
                token=token,
                resident_tg_id=user.id,
                expires_at=expires_at,
            )
            session.add(new_token)
            await session.commit()

        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=temp_{token}"

        await query.edit_message_text(
            f"🔑 Temporary access link (valid for {minutes} minutes):\n\n"
            f"{link}\n\n"
            f"Send this link to your guest. They can use /open to open the door."
        )

    elif action == "use":
        token_str = parts[2]
        user = update.effective_user

        async with await get_session() as session:
            now = datetime.utcnow()
            result = await session.execute(
                select(TempOpenToken).where(
                    and_(
                        TempOpenToken.token == token_str,
                        TempOpenToken.expires_at > now,
                        TempOpenToken.used_at.is_(None),
                    )
                )
            )
            token = result.scalar_one_or_none()

            if not token:
                await query.edit_message_text(
                    "❌ This access link has expired or was already used."
                )
                return

            token.guest_tg_id = user.id
            await session.commit()

        await query.edit_message_text(
            "✅ Access link activated!\n\n"
            "You can now use /open to open the door (while the resident who invited you is at the space)."
        )


async def handle_start_temp(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str
) -> None:
    """Handle /start with temp token."""
    user = update.effective_user

    async with await get_session() as session:
        now = datetime.utcnow()
        result = await session.execute(
            select(TempOpenToken).where(
                and_(
                    TempOpenToken.token == token,
                    TempOpenToken.expires_at > now,
                    TempOpenToken.used_at.is_(None),
                )
            )
        )
        token_obj = result.scalar_one_or_none()

        if not token_obj:
            await update.message.reply_text(
                "❌ This access link has expired or is invalid."
            )
            return

        token_obj.guest_tg_id = user.id
        await session.commit()

    await update.message.reply_text(
        "✅ Access link activated!\n\n"
        "You can now use /open to open the door (while the resident who invited you is at the space)."
    )


def get_callback_handler():
    """Get callback handler for butler module."""
    return CallbackQueryHandler(butler_callback, pattern=r"^butler:")
