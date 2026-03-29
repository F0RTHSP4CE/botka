from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import inject, FromDishka

from botka.services.ups_client import UpsClient

logger = logging.getLogger(__name__)
router = Router(name=__name__)


@router.message(Command("ups"))
@inject
async def ups_status_handler(
    message: Message,
    ups_client: FromDishka[UpsClient],
) -> None:
    if not ups_client.is_configured:
        await message.reply("UPS integration is not configured.")
        return
    try:
        status = await ups_client.get_status()
    except Exception:
        logger.exception("Failed to fetch UPS status")
        await message.reply("Failed to fetch UPS status.")
        return
    await message.reply(status.format_text())
