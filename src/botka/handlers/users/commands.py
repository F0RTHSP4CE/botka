from __future__ import annotations

from html import escape as html_escape
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import UserTier
from botka.handlers.user_links import format_user_link
from botka.services.user_service import UserService

router = Router(name=__name__)


def _get_explicit_reply_user(message: Message):
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None
    if reply.forum_topic_created is not None:
        return None
    return reply.from_user


@router.message(Command("start"))
@inject
async def start_handler(
    message: Message,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    await message.reply("Ready.")


@router.message(Command("user"))
@inject
async def user_handler(
    message: Message,
    command: CommandObject,
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    args = (command.args or "").split()
    if not args:
        target_user = _get_explicit_reply_user(message) or message.from_user
        if target_user is None:
            await message.reply("Cannot determine target user.")
            return
        target_id = target_user.id
        info_user = await user_service.get_user(target_id)
        if info_user is None:
            tier = await user_service.ensure_user(
                target_id,
                target_user.username,
            )
            info_user = await user_service.get_user(target_id)
        else:
            tier = info_user.tier
        await message.reply(
            "User info: {} | id {} | tier {} | username {}".format(
                format_user_link(target_user),
                target_id,
                tier.value,
                html_escape(info_user.username) if info_user else "unknown",
            ),
            disable_web_page_preview=True,
        )
        return

    if len(args) not in (1, 2):
        await message.reply(
            html_escape(
                "Usage: /user [<resident|member|guest> [<telegram_id>]] (or reply to a user message)"
            )
        )
        return

    if len(args) == 1:
        tier_raw = args[0]
        reply_user = _get_explicit_reply_user(message)
        if reply_user is not None:
            target_id = reply_user.id
        elif message.from_user is not None:
            target_id = message.from_user.id
        else:
            await message.reply("Cannot determine target user.")
            return
    else:
        tier_raw, target_id_raw = args
        try:
            target_id = int(target_id_raw)
        except ValueError:
            await message.reply("Invalid telegram id.")
            return

    try:
        tier = UserTier(tier_raw)
    except ValueError:
        await message.reply("Tier must be resident, member, or guest.")
        return
    if user_service.is_bootstrap_resident(target_id) and tier != UserTier.resident:
        await message.reply("Bootstrapped user tiers cannot be changed.")
        return
    updated = await user_service.set_tier(message.from_user.id, target_id, tier)
    if not updated:
        await message.reply("Only residents can change tiers.")
        return
    target_user = await user_service.get_user(target_id)
    target_label = format_user_link(
        telegram_id=target_id,
        username=target_user.username if target_user else None,
    )
    await message.reply(
        "Tier updated: {} is now a {}.".format(target_label, tier.value),
        disable_web_page_preview=True,
    )
