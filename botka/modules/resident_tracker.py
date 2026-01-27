"""Resident tracker module - handles join/leave events in residential chats."""

import logging
from datetime import datetime

from telegram import (
    Update,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ChatMemberHandler, CallbackQueryHandler
from sqlalchemy import select, and_

from ..db import Resident, TgUser, TgUserInChat, get_session

logger = logging.getLogger(__name__)


async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chat member updates to track residency."""
    from ..bot import state

    member_update: ChatMemberUpdated = update.chat_member

    # Ignore bots
    if member_update.new_chat_member.user.is_bot:
        return

    # Check if this is a residential chat
    if member_update.chat.id not in state.config.telegram.chats.residential:
        return

    user = member_update.new_chat_member.user
    was_member = member_update.old_chat_member.status in [
        "member",
        "administrator",
        "creator",
    ]
    is_member = member_update.new_chat_member.status in [
        "member",
        "administrator",
        "creator",
    ]

    # Track seen status
    async with await get_session() as session:
        result = await session.execute(
            select(TgUserInChat).where(
                and_(
                    TgUserInChat.chat_id == member_update.chat.id,
                    TgUserInChat.user_id == user.id,
                )
            )
        )
        record = result.scalar_one_or_none()

        if record:
            record.seen = is_member
        else:
            record = TgUserInChat(
                chat_id=member_update.chat.id, user_id=user.id, seen=is_member
            )
            session.add(record)

        await session.commit()

    # Handle join
    if not was_member and is_member:
        await handle_join(context, member_update)

    # Handle leave
    elif was_member and not is_member:
        await handle_leave(context, member_update)


async def handle_join(
    context: ContextTypes.DEFAULT_TYPE, member_update: ChatMemberUpdated
) -> None:
    """Handle user joining residential chat."""
    from ..bot import state

    user = member_update.new_chat_member.user

    async with await get_session() as session:
        # Check if already resident
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == user.id, Resident.end_date.is_(None))
            )
        )
        if result.scalar_one_or_none():
            return

        # Make them a resident
        new_resident = Resident(tg_id=user.id, begin_date=datetime.utcnow())
        session.add(new_resident)

        # Save user info
        result = await session.execute(select(TgUser).where(TgUser.id == user.id))
        tg_user = result.scalar_one_or_none()

        if tg_user:
            tg_user.username = user.username
            tg_user.first_name = user.first_name
            tg_user.last_name = user.last_name
        else:
            tg_user = TgUser(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            session.add(tg_user)

        await session.commit()

    logger.info(f"New resident: {user.full_name} ({user.id})")


async def handle_leave(
    context: ContextTypes.DEFAULT_TYPE, member_update: ChatMemberUpdated
) -> None:
    """Handle user leaving residential chat."""
    from ..bot import state

    user = member_update.new_chat_member.user

    async with await get_session() as session:
        # Check if user is still seen in any residential chat
        for chat_id in state.config.telegram.chats.residential:
            result = await session.execute(
                select(TgUserInChat).where(
                    and_(
                        TgUserInChat.chat_id == chat_id,
                        TgUserInChat.user_id == user.id,
                        TgUserInChat.seen == True,
                    )
                )
            )
            if result.scalar_one_or_none():
                # Still in another residential chat
                return

        # Check if they're a resident
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == user.id, Resident.end_date.is_(None))
            )
        )
        resident = result.scalar_one_or_none()

        if not resident:
            return

    # Notify admins
    logger.info(f"User {user.full_name} ({user.id}) left all residential chats")

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Stop residency", callback_data=f"res_stop:{user.id}")]]
    )

    text = f"Пользователь {user.full_name} вышел из всех резидентских чатов."

    for admin_id in state.config.telegram.admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


async def resident_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resident management callbacks."""
    from ..bot import is_admin

    query = update.callback_query

    if not await is_admin(query.from_user.id):
        await query.answer(
            "You are not allowed to perform this action.", show_alert=True
        )
        return

    data = query.data
    if not data.startswith("res_stop:"):
        return

    try:
        user_id = int(data.split(":")[1])
    except (ValueError, IndexError):
        return

    async with await get_session() as session:
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == user_id, Resident.end_date.is_(None))
            )
        )
        resident = result.scalar_one_or_none()

        if resident:
            resident.end_date = datetime.utcnow()
            await session.commit()

    await query.answer("Residency stopped.")
    await query.edit_message_reply_markup(reply_markup=None)


def get_chat_member_handler():
    """Get handler for chat member updates."""
    return ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER)


def get_callback_handler():
    """Get callback handler for resident management."""
    return CallbackQueryHandler(resident_callback, pattern=r"^res_stop:")
