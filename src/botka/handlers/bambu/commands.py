from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User, UserTier
from botka.handlers.bambu.utils import status_keyboard
from botka.handlers.menu import Btn
from botka.services.bambu_service import BambuService

router = Router(name=__name__)


async def _do_bambu(
    message: Message,
    bambu_service: BambuService,
    user_record: User | None,
) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can check printer status.")
        return
    if not bambu_service.is_configured:
        await message.reply("Bambu Lab integration is not configured.")
        return
    statuses = await bambu_service.get_all_statuses()
    if not statuses:
        await message.reply("Could not retrieve printer status.")
        return
    text = "\n\n".join(s.format_text() for s in statuses)
    kb = status_keyboard(bambu_service.printer_names)
    await message.reply(text, reply_markup=kb)


@router.message(Command("bambu"))
@inject
async def bambu_status_handler(
    message: Message,
    bambu_service: FromDishka[BambuService],
    user_record: User | None = None,
) -> None:
    await _do_bambu(message, bambu_service, user_record)


@router.message(F.text == Btn.BAMBU, F.chat.type == "private")
@inject
async def menu_bambu_message(
    message: Message,
    bambu_service: FromDishka[BambuService],
    user_record: User | None = None,
) -> None:
    await _do_bambu(message, bambu_service, user_record)
