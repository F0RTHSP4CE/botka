from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import inject, FromDishka

from botka.db.models import User, UserTier
from botka.services.ups_client import UpsClient

logger = logging.getLogger(__name__)
router = Router(name=__name__)


@router.message(Command("ups"))
@inject
async def ups_status_handler(
    message: Message,
    ups_client: FromDishka[UpsClient],
    user_record: User | None = None,
) -> None:
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can check UPS status.")
        return
    if not ups_client.is_configured:
        await message.reply("UPS integration is not configured.")
        return
    try:
        status = await ups_client.get_status()
    except Exception:
        logger.exception("Failed to fetch UPS status")
        await message.reply("Failed to fetch UPS status.")
        return
    if status is None:
        await message.reply("UPS device is unreachable.")
        return
    await message.reply(status.format_text())
