from __future__ import annotations

from datetime import timezone
from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.handlers.menu import Btn
from botka.handlers.user_links import format_user_link
from botka.db.models import User, UserTier
from botka.services.mac_tracker_service import MacTrackerService
from botka.services.user_service import UserService

router = Router(name=__name__)


def _get_explicit_reply_user(message: Message):
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None
    if reply.forum_topic_created is not None:
        return None
    return reply.from_user


def _build_link_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Register device", url=url)]]
    )


def _build_clear_keyboard(
    target_user_id: int, actor_telegram_id: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Confirm clear",
                    callback_data=f"mac_clear:{target_user_id}:{actor_telegram_id}",
                )
            ]
        ]
    )


async def _do_mac(
    message: Message,
    settings: Settings,
    mac_tracker: MacTrackerService,
    user_record: User | None,
    sender_telegram_id: int,
) -> None:
    if not settings.mac_tracker_base_url:
        await message.reply("MAC tracker is not configured.")
        return
    if user_record is None:
        await message.reply("Could not load your user record.")
        return
    token = await mac_tracker.create_token(user_record.id)
    url = settings.mac_tracker_base_url.rstrip("/") + f"/mac/{token}"
    try:
        await message.bot.send_message(
            chat_id=sender_telegram_id,
            text="Open the link below and tap the button to register your device.",
            reply_markup=_build_link_keyboard(url),
            disable_web_page_preview=True,
        )
        if message.chat.id != sender_telegram_id:
            await message.reply("Sent you a personal link in DM.")
    except Exception:
        await message.reply(
            "I couldn't send you a DM. Please start a chat with the bot and try again."
        )


async def _do_status(
    message: Message,
    user_service: UserService,
    mac_tracker: MacTrackerService,
    user_record: User | None,
) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can view who is in the space.")
        return
    presence = await mac_tracker.list_present_users()
    if not presence:
        await message.reply("No one is currently in the space.")
        return
    lease_seen_map = await mac_tracker.get_active_lease_seen_map()
    user_ids = [item.user_id for item in presence]
    users = await user_service.list_users_by_ids(user_ids)
    user_map = {user.id: user for user in users}
    lines = []
    for item in presence:
        user = user_map.get(item.user_id)
        if user is not None:
            label = format_user_link(
                telegram_id=user.telegram_id,
                username=user.username,
            )
        else:
            label = html_escape(f"user {item.user_id}")
        seen_raw = None
        if item.mac_address:
            seen_raw = lease_seen_map.get(item.mac_address)
        if seen_raw:
            lines.append(f"• {label} (seen {html_escape(seen_raw)})")
        else:
            lines.append(f"• {label}")
    await message.reply(
        "Currently in the space:\n" + "\n".join(lines),
        disable_web_page_preview=True,
    )


@router.message(Command("mac"))
@inject
async def mac_link_handler(
    message: Message,
    settings: FromDishka[Settings],
    user_service: FromDishka[UserService],
    mac_tracker: FromDishka[MacTrackerService],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    await _do_mac(message, settings, mac_tracker, user_record, message.from_user.id)


@router.message(Command("status"))
@inject
async def status_handler(
    message: Message,
    user_service: FromDishka[UserService],
    mac_tracker: FromDishka[MacTrackerService],
    user_record: User | None = None,
) -> None:
    await _do_status(message, user_service, mac_tracker, user_record)


@router.message(F.text == Btn.STATUS, F.chat.type == "private")
@inject
async def menu_status_message(
    message: Message,
    user_service: FromDishka[UserService],
    mac_tracker: FromDishka[MacTrackerService],
    user_record: User | None = None,
) -> None:
    await _do_status(message, user_service, mac_tracker, user_record)


@router.message(Command("mac_clear"))
@inject
async def mac_clear_handler(
    message: Message,
    command: CommandObject,
    user_service: FromDishka[UserService],
    mac_tracker: FromDishka[MacTrackerService],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    target_id: int | None = None
    args = (command.args or "").split()
    if args:
        try:
            target_id = int(args[0])
        except ValueError:
            await message.reply("Invalid telegram id.")
            return
    if target_id is None:
        reply_user = _get_explicit_reply_user(message)
        if reply_user is not None:
            target_id = reply_user.id
        else:
            target_id = message.from_user.id
    if target_id != message.from_user.id:
        if user_record is None or user_record.tier != UserTier.resident:
            await message.reply("Only residents can clear other users.")
            return
    target_user = await user_service.get_user(target_id)
    if target_user is None:
        await message.reply("User not found.")
        return
    macs = await mac_tracker.list_user_macs(target_user.id)
    if not macs:
        await message.reply("No MAC addresses assigned for this user.")
        return
    mac_list = "\n".join(f"• {html_escape(mac)}" for mac in macs)
    await message.reply(
        "Clear MAC assignments for user {}?\n{}".format(
            format_user_link(
                telegram_id=target_user.telegram_id, username=target_user.username
            ),
            mac_list,
        ),
        reply_markup=_build_clear_keyboard(target_user.id, message.from_user.id),
    )
