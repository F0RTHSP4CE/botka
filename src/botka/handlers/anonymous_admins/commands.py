from __future__ import annotations

import logging
from asyncio import sleep as cooldown_sleep
from collections.abc import Sequence
from time import monotonic

from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import ChatMemberUnion, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.services.telegram_retry import call_with_retry_after
from botka.services.user_service import UserService

router = Router(name=__name__)
logger = logging.getLogger(__name__)
ADMIN_OPERATION_COOLDOWN_SECONDS = 1.0
COMMAND_COOLDOWN_SECONDS = 5 * 60
_command_last_run_at: dict[int, float] = {}

ADMIN_PERMISSION_FIELDS = (
    "is_anonymous",
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
    "can_post_messages",
    "can_edit_messages",
    "can_pin_messages",
    "can_manage_topics",
    "can_manage_direct_messages",
)
DEMOTION_PERMISSIONS = {field: False for field in ADMIN_PERMISSION_FIELDS}


def _has_command_access(user_record: User | None) -> bool:
    return user_record is not None and user_record.tier in (
        UserTier.resident,
        UserTier.member,
    )


def _is_supergroup(message: Message) -> bool:
    return message.chat.type == ChatType.SUPERGROUP


def _is_anonymous_chat_admin(message: Message) -> bool:
    """Identify a message Telegram sent on behalf of this same supergroup."""

    return (
        message.sender_chat is not None
        and message.sender_chat.id == message.chat.id
    )


def _is_allowed_group(message: Message, settings: Settings) -> bool:
    return message.chat.id in settings.allowed_anon_group_ids


def _claim_command_run(chat_id: int) -> bool:
    now = monotonic()
    last_run_at = _command_last_run_at.get(chat_id)
    if last_run_at is not None and now - last_run_at < COMMAND_COOLDOWN_SECONDS:
        return False
    _command_last_run_at[chat_id] = now
    return True


async def _promote_chat_member(
    message: Message,
    telegram_id: int,
    **permissions: bool | None,
) -> None:
    await call_with_retry_after(
        lambda: message.bot.promote_chat_member(
            message.chat.id,
            telegram_id,
            **permissions,
        ),
        description=(
            f"Admin permission change for {telegram_id} in {message.chat.id}"
        ),
    )


async def _get_chat_administrators(message: Message) -> Sequence[ChatMemberUnion]:
    return await call_with_retry_after(
        lambda: message.bot.get_chat_administrators(message.chat.id),
        description=f"Administrator list for {message.chat.id}",
    )


@router.message(Command("anon"))
@inject
async def anon_handler(
    message: Message,
    user_service: FromDishka[UserService],
    settings: FromDishka[Settings],
    user_record: User | None = None,
) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning(
            "Could not delete /anon message in chat %s",
            message.chat.id,
            exc_info=True,
        )

    if not _is_supergroup(message) or not _is_allowed_group(message, settings):
        return
    if not _has_command_access(user_record):
        return
    if not _claim_command_run(message.chat.id):
        return

    for telegram_id in await user_service.list_resident_ids():
        try:
            member = await message.bot.get_chat_member(message.chat.id, telegram_id)
        except TelegramAPIError:
            logger.warning(
                "Could not inspect resident %s in chat %s",
                telegram_id,
                message.chat.id,
                exc_info=True,
            )
            continue

        status = member.status
        if status == ChatMemberStatus.CREATOR:
            continue
        if status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            continue
        if status == ChatMemberStatus.ADMINISTRATOR and not member.can_be_edited:
            continue

        try:
            await _promote_chat_member(
                message,
                telegram_id,
                is_anonymous=True,
                can_manage_chat=True,
            )
        except TelegramAPIError:
            logger.warning(
                "Could not enable anonymous admin for resident %s in chat %s",
                telegram_id,
                message.chat.id,
                exc_info=True,
            )
            continue
        await cooldown_sleep(ADMIN_OPERATION_COOLDOWN_SECONDS)


@router.message(Command("deanon"))
@inject
async def deanon_handler(
    message: Message,
    settings: FromDishka[Settings],
    user_record: User | None = None,
) -> None:
    # This intentionally precedes even authorization and chat validation.
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning(
            "Could not delete /deanon message in chat %s",
            message.chat.id,
            exc_info=True,
        )

    if not _is_supergroup(message) or not _is_allowed_group(message, settings):
        return
    if not _has_command_access(user_record) and not _is_anonymous_chat_admin(message):
        return
    if not _claim_command_run(message.chat.id):
        return

    try:
        administrators = await _get_chat_administrators(message)
    except TelegramAPIError:
        logger.warning(
            "Could not list administrators in chat %s",
            message.chat.id,
            exc_info=True,
        )
        return

    for administrator in administrators:
        if administrator.status != ChatMemberStatus.ADMINISTRATOR:
            continue
        if not administrator.is_anonymous or not administrator.can_be_edited:
            continue
        try:
            await _promote_chat_member(
                message,
                administrator.user.id,
                **DEMOTION_PERMISSIONS,
            )
        except TelegramAPIError:
            logger.warning(
                "Could not dismiss anonymous administrator %s in chat %s",
                administrator.user.id,
                message.chat.id,
                exc_info=True,
            )
            continue
        await cooldown_sleep(ADMIN_OPERATION_COOLDOWN_SECONDS)
