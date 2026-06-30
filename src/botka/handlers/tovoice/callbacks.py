from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from botka.handlers.tovoice.commands import _active_conversions

logger = logging.getLogger(__name__)
router = Router(name=__name__)


@router.callback_query(F.data.startswith("tovoice_cancel:"))
async def tovoice_cancel_callback(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer("Invalid request.", show_alert=True)
        return

    job_id = callback.data.removeprefix("tovoice_cancel:")
    cancel_event = _active_conversions.get(job_id)

    if cancel_event is None:
        # Conversion already finished (success or failure).
        try:
            await callback.answer("The conversion has already finished.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    cancel_event.set()
    try:
        await callback.answer("Cancelling conversion…")
    except TelegramBadRequest:
        pass
