"""Polls module for tracking non-anonymous polls."""

import json
import logging

from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    PollAnswerHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from sqlalchemy import select

from ..db import TrackedPoll, Resident, TgUser, get_session

logger = logging.getLogger(__name__)


async def handle_poll_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle new poll messages."""
    from ..bot import is_resident

    message = update.message
    poll = message.poll
    user = message.from_user

    if not poll:
        return

    # Only track polls starting with !
    if not poll.question.startswith("!"):
        return

    # Check requirements
    errors = []
    if poll.total_voter_count != 0:
        errors.append("❌ Poll already has votes")
    if poll.is_closed:
        errors.append("❌ Poll is closed")
    if poll.is_anonymous:
        errors.append("❌ Poll is anonymous")
    if poll.type != Poll.REGULAR:
        errors.append("❌ Poll is not regular (quiz polls not supported)")
    if not await is_resident(user.id):
        errors.append("❌ You are not a resident")

    if errors:
        await message.reply_text(
            "It seems you tried to create a bot-tracked poll, "
            f"but it doesn't meet all requirements:\n" + "\n".join(errors)
        )
        return

    # Create new poll without the ! prefix
    question = poll.question.lstrip("!").strip()
    options = [opt.text for opt in poll.options]

    new_poll_msg = await context.bot.send_poll(
        chat_id=message.chat_id,
        question=question,
        options=options,
        is_anonymous=False,
        allows_multiple_answers=poll.allows_multiple_answers,
        message_thread_id=message.message_thread_id,
        reply_to_message_id=(
            message.reply_to_message.message_id if message.reply_to_message else None
        ),
    )

    # Delete original message
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Failed to delete original poll: {e}")
        try:
            await context.bot.delete_message(message.chat_id, new_poll_msg.message_id)
        except:
            pass
        return

    # Get non-voters
    non_voters = await get_non_voters([])

    # Send info message
    info_text = format_poll_info(user.id, user.first_name, non_voters, 0)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Refresh", callback_data=f"poll:refresh:{new_poll_msg.poll.id}"
                )
            ]
        ]
    )

    info_msg = await context.bot.send_message(
        chat_id=message.chat_id,
        text=info_text,
        parse_mode="HTML",
        reply_to_message_id=new_poll_msg.message_id,
        reply_markup=keyboard,
        message_thread_id=message.message_thread_id,
    )

    # Save to database
    async with await get_session() as session:
        tracked = TrackedPoll(
            tg_poll_id=new_poll_msg.poll.id,
            creator_id=user.id,
            info_chat_id=info_msg.chat_id,
            info_message_id=info_msg.message_id,
            voted_users=json.dumps([]),
        )
        session.add(tracked)
        await session.commit()


async def handle_poll_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle poll answer updates."""
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id
    user_id = poll_answer.user.id

    async with await get_session() as session:
        result = await session.execute(
            select(TrackedPoll).where(TrackedPoll.tg_poll_id == poll_id)
        )
        tracked = result.scalar_one_or_none()

        if not tracked:
            return

        voted_users = json.loads(tracked.voted_users)

        if poll_answer.option_ids:  # User voted
            if user_id not in voted_users:
                voted_users.append(user_id)
        else:  # User retracted vote
            if user_id in voted_users:
                voted_users.remove(user_id)

        tracked.voted_users = json.dumps(voted_users)
        await session.commit()

        # Update info message
        non_voters = await get_non_voters(voted_users)

        # Get creator info
        result = await session.execute(
            select(TgUser).where(TgUser.id == tracked.creator_id)
        )
        creator = result.scalar_one_or_none()
        creator_name = creator.first_name if creator else "Unknown"

        info_text = format_poll_info(
            tracked.creator_id, creator_name, non_voters, len(voted_users)
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Refresh", callback_data=f"poll:refresh:{poll_id}"
                    )
                ]
            ]
        )

        try:
            await context.bot.edit_message_text(
                chat_id=tracked.info_chat_id,
                message_id=tracked.info_message_id,
                text=info_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.debug(f"Failed to update poll info: {e}")


async def poll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle poll callback buttons."""
    query = update.callback_query

    data = query.data
    if not data.startswith("poll:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    action = parts[1]
    poll_id = parts[2]

    if action == "refresh":
        async with await get_session() as session:
            result = await session.execute(
                select(TrackedPoll).where(TrackedPoll.tg_poll_id == poll_id)
            )
            tracked = result.scalar_one_or_none()

            if not tracked:
                await query.answer("Poll not found")
                return

            voted_users = json.loads(tracked.voted_users)
            non_voters = await get_non_voters(voted_users)

            result = await session.execute(
                select(TgUser).where(TgUser.id == tracked.creator_id)
            )
            creator = result.scalar_one_or_none()
            creator_name = creator.first_name if creator else "Unknown"

            info_text = format_poll_info(
                tracked.creator_id, creator_name, non_voters, len(voted_users)
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Refresh", callback_data=f"poll:refresh:{poll_id}"
                        )
                    ]
                ]
            )

            await query.edit_message_text(
                text=info_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            await query.answer("Updated!")


async def get_non_voters(voted_user_ids: list[int]) -> list[tuple[int, str]]:
    """Get list of residents who haven't voted."""
    async with await get_session() as session:
        result = await session.execute(
            select(Resident, TgUser)
            .outerjoin(TgUser, Resident.tg_id == TgUser.id)
            .where(Resident.end_date.is_(None))
        )
        rows = result.all()

    non_voters = []
    for resident, tg_user in rows:
        if resident.tg_id not in voted_user_ids:
            name = tg_user.first_name if tg_user else f"User {resident.tg_id}"
            non_voters.append((resident.tg_id, name))

    return non_voters


def format_poll_info(
    creator_id: int,
    creator_name: str,
    non_voters: list[tuple[int, str]],
    vote_count: int,
) -> str:
    """Format poll info message."""
    text = f'📊 Poll by <a href="tg://user?id={creator_id}">{creator_name}</a>\n'
    text += f"Votes: {vote_count}\n\n"

    if non_voters:
        text += "<b>Haven't voted:</b>\n"
        for user_id, name in non_voters[:20]:  # Limit to 20
            text += f'• <a href="tg://user?id={user_id}">{name}</a>\n'
        if len(non_voters) > 20:
            text += f"... and {len(non_voters) - 20} more\n"
    else:
        text += "✅ Everyone has voted!"

    return text


def get_poll_message_handler():
    """Get handler for poll messages."""
    return MessageHandler(filters.POLL & filters.Regex(r"^!"), handle_poll_message)


def get_poll_answer_handler():
    """Get handler for poll answers."""
    return PollAnswerHandler(handle_poll_answer)


def get_callback_handler():
    """Get callback handler for polls."""
    return CallbackQueryHandler(poll_callback, pattern=r"^poll:")
