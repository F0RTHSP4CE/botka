"""Fridge POS slash-command handler.

/fridge  → confirmation button → remote charge using the caller's Telegram username
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User, UserTier
from botka.handlers.menu import Btn
from botka.services.fridge_client import FridgeClient

router = Router(name=__name__)

_NOT_CONFIGURED = "Fridge integration is not configured."


def _build_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Confirm",
                    callback_data=f"fridge_charge:{user_id}",
                )
            ]
        ]
    )


async def _do_fridge(
    message: Message,
    fridge: FridgeClient,
    user_record: User | None,
    sender_id: int,
    sender_username: str | None,
) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can use the fridge.")
        return
    if not fridge.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    if not sender_username:
        await message.reply("You need a Telegram username to use the fridge.")
        return
    await message.reply(
        "Confirm fridge opening (you will be charged).",
        reply_markup=_build_confirm_keyboard(sender_id),
    )


@router.message(Command("fridge"))
@inject
async def fridge_handler(
    message: Message,
    fridge: FromDishka[FridgeClient],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    await _do_fridge(
        message,
        fridge,
        user_record,
        message.from_user.id,
        message.from_user.username,
    )


@router.message(F.text == Btn.FRIDGE, F.chat.type == "private")
@inject
async def menu_fridge_message(
    message: Message,
    fridge: FromDishka[FridgeClient],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        return
    await _do_fridge(
        message,
        fridge,
        user_record,
        message.from_user.id,
        message.from_user.username,
    )
