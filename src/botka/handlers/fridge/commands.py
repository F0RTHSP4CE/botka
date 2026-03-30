"""Fridge POS slash-command handler.

/fridge  → confirmation button → remote charge using the caller's Telegram username
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

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


@router.message(Command("fridge"))
@inject
async def fridge_handler(
    message: Message,
    fridge: FromDishka[FridgeClient],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not fridge.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    username = message.from_user.username
    if not username:
        await message.reply("You need a Telegram username to use the fridge.")
        return

    await message.reply(
        "Confirm fridge opening (you will be charged).",
        reply_markup=_build_confirm_keyboard(message.from_user.id),
    )
