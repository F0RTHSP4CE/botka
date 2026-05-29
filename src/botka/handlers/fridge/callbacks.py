"""Fridge POS callback handler.

Callback data format: fridge_charge:{user_id}
Only the user who triggered the command can confirm.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.services.fridge_client import FridgeClient

logger = logging.getLogger(__name__)
router = Router(name=__name__)


@router.callback_query(F.data.startswith("fridge_charge:"))
@inject
async def fridge_charge_callback(
    callback: CallbackQuery,
    fridge: FromDishka[FridgeClient],
) -> None:
    if callback.from_user is None or callback.data is None:
        await callback.answer("Invalid request.", show_alert=True)
        return

    try:
        allowed_user_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid callback data.", show_alert=True)
        return

    if callback.from_user.id != allowed_user_id:
        await callback.answer("This button is not for you.", show_alert=True)
        return

    if not fridge.is_configured:
        await callback.answer("Fridge service not configured.", show_alert=True)
        if callback.message is not None:
            await callback.message.edit_text(
                "Fridge service is not configured.", reply_markup=None
            )
        return

    username = callback.from_user.username
    if not username:
        await callback.answer(
            "You need a Telegram username to use the fridge.", show_alert=True
        )
        return

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text("⏳ Opening the fridge…", reply_markup=None)

    try:
        result = await fridge.remote_charge(username)
    except Exception:
        logger.exception("Fridge remote-charge failed for @%s", username)
        if callback.message is not None:
            await callback.message.edit_text(
                "❌ Failed to reach the fridge.", reply_markup=None
            )
        await callback.answer("Failed to reach the fridge.", show_alert=True)
        return

    charge_line = ""
    if result.charged and result.amount is not None and result.currency:
        charge_line = f"\nCharged: {result.amount} {result.currency}"

    if result.ok:
        balance_line = ""
        if result.balance_completed is not None and result.currency:
            balance_line = (
                f"\nYour balance: {result.balance_completed} {result.currency}"
            )
            if result.balance_draft is not None:
                balance_line += f" (draft: {result.balance_draft} {result.currency})"
        text = f"🥤 Fridge opened.{charge_line}{balance_line}"
        if callback.message is not None:
            await callback.message.edit_text(text, reply_markup=None)
        await callback.answer("Fridge opened!")
    else:
        error_parts = []
        if result.error_code is not None:
            error_parts.append(str(result.error_code))
        if result.error is not None:
            error_parts.append(str(result.error))
        error_detail = ": " + ", ".join(error_parts) if error_parts else ""
        text = f"❌ Fridge charge failed{error_detail}."
        if callback.message is not None:
            await callback.message.edit_text(text, reply_markup=None)
        await callback.answer("Charge failed.", show_alert=True)
