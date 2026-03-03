"""Callback handlers for refinance inline keyboards.

Callback data format:
  rf_tx:confirm:{tx_id}:{actor_eid}:{actor_tid}        → sender confirms draft → completed
  rf_tx:cancel:{tx_id}:{actor_eid}:{actor_tid}         → sender cancels → delete tx
  rf_req:confirm:{tx_id}:{payer_eid}:{payer_tid|0}     → payer pays → completed
  rf_req:cancel:{tx_id}:{payer_eid}:{payer_tid|0}      → payer denies → delete tx
  rf_req:author_cancel:{tx_id}:{req_eid}:{req_tid}     → requester cancels → delete tx
"""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.services.refinance_client import RefinanceClient

router = Router(name=__name__)


def _parse_tx_parts(data: str) -> tuple[int, int, int | None] | None:
    """Parse '{tx_id}:{entity_id}:{telegram_id|0}' from the end of callback data."""
    parts = data.split(":")
    try:
        tx_id = int(parts[-3])
        entity_id = int(parts[-2])
        raw_tid = int(parts[-1])
        telegram_id = raw_tid or None
        return tx_id, entity_id, telegram_id
    except (IndexError, ValueError):
        return None


@router.callback_query(F.data.startswith("rf_tx:confirm:"))
@inject
async def transfer_confirm_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    parsed = _parse_tx_parts(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback data.", show_alert=True)
        return
    tx_id, actor_entity_id, actor_telegram_id = parsed

    if actor_telegram_id is not None and callback.from_user.id != actor_telegram_id:
        await callback.answer("Only the sender can confirm this transfer.", show_alert=True)
        return

    try:
        tx = await refinance.update_transaction_status(
            actor_entity_id=actor_entity_id,
            transaction_id=tx_id,
            status="completed",
        )
    except Exception as exc:
        await callback.answer(f"Transfer failed: {str(exc)[:100]}", show_alert=True)
        return

    body = (
        f"✅ Transfer #{tx['id']} confirmed: "
        f"<b>{html.escape(str(tx.get('amount', '')))} "
        f"{html.escape((tx.get('currency') or '').upper())}</b> sent."
    )
    if tx.get("comment"):
        body += f"\nComment: <i>{html.escape(tx['comment'])}</i>"
    await callback.message.edit_text(body, reply_markup=None)
    await callback.answer("Transfer confirmed.")


@router.callback_query(F.data.startswith("rf_tx:cancel:"))
@inject
async def transfer_cancel_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    parsed = _parse_tx_parts(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback data.", show_alert=True)
        return
    tx_id, actor_entity_id, actor_telegram_id = parsed

    if actor_telegram_id is not None and callback.from_user.id != actor_telegram_id:
        await callback.answer("Only the sender can cancel this transfer.", show_alert=True)
        return

    try:
        await refinance.delete_transaction(
            actor_entity_id=actor_entity_id,
            transaction_id=tx_id,
        )
    except Exception as exc:
        await callback.answer(f"Failed to cancel: {str(exc)[:100]}", show_alert=True)
        return

    await callback.message.edit_text(f"❌ Transfer #{tx_id} cancelled.", reply_markup=None)
    await callback.answer("Cancelled.")


# ------------------------------------------------------------------ #
# Payment request confirm / deny (payer-facing)                         #
# ------------------------------------------------------------------ #


@router.callback_query(F.data.startswith("rf_req:confirm:"))
@inject
async def request_confirm_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    # rf_req:confirm:{tx_id}:{payer_entity_id}:{payer_telegram_id|0}
    parts = (callback.data or "").split(":")
    try:
        tx_id = int(parts[2])
        payer_entity_id = int(parts[3])
        payer_telegram_id = int(parts[4]) or None
    except (IndexError, ValueError):
        await callback.answer("Invalid callback data.", show_alert=True)
        return

    if payer_telegram_id is not None and callback.from_user.id != payer_telegram_id:
        await callback.answer(
            "Only the person being charged can approve this request.", show_alert=True
        )
        return

    try:
        tx = await refinance.update_transaction_status(
            actor_entity_id=payer_entity_id,
            transaction_id=tx_id,
            status="completed",
        )
    except Exception as exc:
        await callback.answer(f"Payment failed: {str(exc)[:100]}", show_alert=True)
        return

    body = (
        f"✅ Paid tx #{tx['id']}: "
        f"<b>{html.escape(str(tx.get('amount', '')))} {html.escape((tx.get('currency') or '').upper())}</b> sent."
    )
    if tx.get("comment"):
        body += f"\nComment: <i>{html.escape(tx['comment'])}</i>"
    await callback.message.edit_text(body, reply_markup=None)
    await callback.answer("Payment sent.")


@router.callback_query(F.data.startswith("rf_req:cancel:"))
@inject
async def request_cancel_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    try:
        tx_id = int(parts[2])
        payer_entity_id = int(parts[3])
        payer_telegram_id = int(parts[4]) or None
    except (IndexError, ValueError):
        await callback.answer("Invalid callback data.", show_alert=True)
        return

    if payer_telegram_id is not None and callback.from_user.id != payer_telegram_id:
        await callback.answer(
            "Only the person being charged can deny this request.", show_alert=True
        )
        return

    try:
        await refinance.delete_transaction(
            actor_entity_id=payer_entity_id,
            transaction_id=tx_id,
        )
    except Exception as exc:
        await callback.answer(f"Failed to delete request: {str(exc)[:100]}", show_alert=True)
        return

    await callback.message.edit_text(f"❌ Payment request #{tx_id} denied.", reply_markup=None)
    await callback.answer("Denied.")


# ------------------------------------------------------------------ #
# Request author cancel (requester-facing)                              #
# ------------------------------------------------------------------ #


@router.callback_query(F.data.startswith("rf_req:author_cancel:"))
@inject
async def request_author_cancel_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    parsed = _parse_tx_parts(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback data.", show_alert=True)
        return
    tx_id, requester_entity_id, requester_telegram_id = parsed

    if requester_telegram_id is not None and callback.from_user.id != requester_telegram_id:
        await callback.answer("Only the requester can cancel this request.", show_alert=True)
        return

    try:
        await refinance.delete_transaction(
            actor_entity_id=requester_entity_id,
            transaction_id=tx_id,
        )
    except Exception as exc:
        await callback.answer(f"Failed to cancel: {str(exc)[:100]}", show_alert=True)
        return

    await callback.message.edit_text(f"❌ Payment request #{tx_id} cancelled.", reply_markup=None)
    await callback.answer("Cancelled.")


# ------------------------------------------------------------------ #
# Deposit payment check                                                 #
# ------------------------------------------------------------------ #


@router.callback_query(F.data.startswith("rf_dep:check:"))
@inject
async def deposit_check_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return

    # rf_dep:check:{deposit_id}:{entity_id}:{telegram_id}
    parsed = _parse_tx_parts(callback.data or "")
    if parsed is None:
        await callback.answer("Invalid callback data.", show_alert=True)
        return
    deposit_id, entity_id, telegram_id = parsed

    if telegram_id is not None and callback.from_user.id != telegram_id:
        await callback.answer("Only the depositor can check this deposit.", show_alert=True)
        return

    try:
        deposit = await refinance.get_deposit(entity_id, deposit_id)
    except Exception as exc:
        await callback.answer(f"Failed to check: {str(exc)[:100]}", show_alert=True)
        return

    status = (deposit.get("status") or "").lower()
    if status == "completed":
        amount = deposit.get("amount", "")
        currency = (deposit.get("currency") or "").upper()
        await callback.message.edit_text(
            f"✅ Deposit #{deposit_id} paid: "
            f"<b>{html.escape(str(amount))} {html.escape(currency)}</b>.",
            reply_markup=None,
        )
        await callback.answer("Payment received!")
    elif status in ("failed", "cancelled"):
        await callback.message.edit_text(
            f"❌ Deposit #{deposit_id} {status}.", reply_markup=None
        )
        await callback.answer(f"Deposit {status}.")
    else:
        await callback.answer("Not paid yet.")
