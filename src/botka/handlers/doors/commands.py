from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import inject

from botka.db.models import User, UserTier
from botka.handlers.menu import Btn
from botka.handlers.doors.utils import (
    DOOR_BOTH_ID,
    DOOR_GATE_ID,
    DOOR_MAIN_ID,
    build_open_keyboard,
)

router = Router(name=__name__)


async def _do_open(message: Message, user_record: User | None) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply(
            "Only residents and members can open the main door.",
        )
        return
    await message.reply(
        "Confirm opening the main door and gate.",
        reply_markup=build_open_keyboard(DOOR_BOTH_ID),
    )


async def _do_open_gate(message: Message) -> None:
    await message.reply(
        "Confirm opening the gate.",
        reply_markup=build_open_keyboard(DOOR_GATE_ID),
    )


@router.message(Command("open"))
@inject
async def open_main_door_handler(
    message: Message,
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply(html.escape("Unknown user."))
        return
    await _do_open(message, user_record)


@router.message(Command("open_gate"))
@inject
async def open_gate_handler(
    message: Message,
) -> None:
    if message.from_user is None:
        await message.reply(html.escape("Unknown user."))
        return
    await _do_open_gate(message)


@router.message(F.text == Btn.OPEN, F.chat.type == "private")
@inject
async def menu_open_message(
    message: Message,
    user_record: User | None = None,
) -> None:
    await _do_open(message, user_record)


@router.message(F.text == Btn.OPEN_GATE, F.chat.type == "private")
@inject
async def menu_open_gate_message(
    message: Message,
) -> None:
    await _do_open_gate(message)
