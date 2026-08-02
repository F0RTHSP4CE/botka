from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import ChatMemberAdministrator, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import AnonymousAdminSnapshot, User, UserTier
from botka.services.anonymous_admin_service import AnonymousAdminService

router = Router(name=__name__)
logger = logging.getLogger(__name__)

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


def _administrator_permissions(
    member: ChatMemberAdministrator,
) -> dict[str, bool | None]:
    return {field: getattr(member, field) for field in ADMIN_PERMISSION_FIELDS}


def _restore_permissions(
    snapshot: AnonymousAdminSnapshot,
) -> dict[str, bool | None]:
    if snapshot.was_administrator:
        # Ignore unknown fields if a snapshot outlives an aiogram/Bot API upgrade.
        return {
            field: value
            for field, value in snapshot.permissions.items()
            if field in ADMIN_PERMISSION_FIELDS and value is not None
        }
    return {field: False for field in ADMIN_PERMISSION_FIELDS}


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


@router.message(Command("anon"))
@inject
async def anon_handler(
    message: Message,
    service: FromDishka[AnonymousAdminService],
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

    promoted = skipped = failed = 0
    for telegram_id in await service.list_resident_ids():
        try:
            member = await message.bot.get_chat_member(message.chat.id, telegram_id)
        except TelegramAPIError:
            logger.warning(
                "Could not inspect resident %s in chat %s",
                telegram_id,
                message.chat.id,
                exc_info=True,
            )
            failed += 1
            continue

        status = member.status
        if status == ChatMemberStatus.CREATOR:
            skipped += 1
            continue
        if status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            skipped += 1
            continue
        if status == ChatMemberStatus.ADMINISTRATOR and not member.can_be_edited:
            skipped += 1
            continue

        snapshot = await service.get_snapshot(message.chat.id, telegram_id)
        if snapshot is None:
            was_administrator = status == ChatMemberStatus.ADMINISTRATOR
            permissions = (
                _administrator_permissions(member) if was_administrator else {}
            )
            await service.save_snapshot(
                message.chat.id,
                telegram_id,
                was_administrator=was_administrator,
                permissions=permissions,
            )

        try:
            await message.bot.promote_chat_member(
                message.chat.id,
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
            failed += 1
            continue
        promoted += 1



@router.message(Command("deanon"))
@inject
async def deanon_handler(
    message: Message,
    service: FromDishka[AnonymousAdminService],
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

    restored = failed = 0
    snapshots = await service.list_snapshots(message.chat.id)
    for snapshot in snapshots:
        try:
            await message.bot.promote_chat_member(
                message.chat.id,
                snapshot.telegram_id,
                **_restore_permissions(snapshot),
            )
        except TelegramAPIError:
            logger.warning(
                "Could not restore admin permissions for resident %s in chat %s",
                snapshot.telegram_id,
                message.chat.id,
                exc_info=True,
            )
            failed += 1
            continue
        await service.delete_snapshot(snapshot)
        restored += 1
