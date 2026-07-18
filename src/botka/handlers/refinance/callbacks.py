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
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.refinance.commands import _build_balance_message, _do_deposit
from botka.handlers.refinance.dialogs import start_deposit_dialog
from botka.handlers.refinance.invoice_ui import (
    affordable_currencies,
    build_payment_view,
    format_notification,
    format_receipt,
    format_review,
    notification_keyboard,
    payment_items,
    review_keyboard,
    room_keyboard,
    selectable_room_recipient,
)
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


async def _authorized_invoice(
    callback: CallbackQuery,
    refinance: RefinanceClient,
    invoice_id: int,
    expected_telegram_id: int,
) -> tuple[dict, dict] | None:
    if callback.from_user is None:
        await callback.answer("Cannot determine sender.", show_alert=True)
        return None
    if callback.from_user.id != expected_telegram_id:
        await callback.answer("This invoice belongs to another user.", show_alert=True)
        return None
    try:
        entity = await refinance.get_or_link_entity(
            callback.from_user.id, callback.from_user.username
        )
        if entity is None:
            await callback.answer(
                "Your Telegram account is not linked to refinance.", show_alert=True
            )
            return None
        invoice = await refinance.get_invoice(entity["id"], invoice_id)
    except Exception as exc:
        await callback.answer(
            f"Could not load invoice: {str(exc)[:100]}", show_alert=True
        )
        return None
    if invoice.get("from_entity_id") != entity["id"]:
        await callback.answer("This invoice belongs to another user.", show_alert=True)
        return None
    raw_owner_id = ((invoice.get("from_entity") or {}).get("auth") or {}).get(
        "telegram_id"
    )
    if raw_owner_id not in (None, ""):
        try:
            if int(raw_owner_id) != callback.from_user.id:
                await callback.answer(
                    "This invoice belongs to another user.", show_alert=True
                )
                return None
        except (TypeError, ValueError):
            await callback.answer(
                "Invoice owner is not linked correctly.", show_alert=True
            )
            return None
    if invoice.get("status") != "pending":
        if callback.message is not None and isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"✅ Invoice #{invoice_id} is already {invoice.get('status', 'settled')}.",
                reply_markup=None,
            )
        await callback.answer("This invoice is no longer pending.")
        return None
    return entity, invoice


async def _resolve_room(
    refinance: RefinanceClient,
    entity_id: int,
    invoice: dict,
    room_id: int,
) -> dict | None:
    if room_id == 0:
        return None
    tag_id, _ = selectable_room_recipient(invoice)
    if tag_id is None:
        raise ValueError("This invoice has no selectable recipient")
    rooms = await refinance.get_entities_by_tag(entity_id, tag_id, active=True)
    return next((room for room in rooms if int(room["id"]) == room_id), None)


async def _show_room_picker(
    callback: CallbackQuery,
    refinance: RefinanceClient,
    *,
    entity: dict,
    invoice: dict,
    requested_room_id: int,
    origin: str,
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    tag_id, default_room_id = selectable_room_recipient(invoice)
    if tag_id is None:
        await callback.answer("This invoice has no room choice.", show_alert=True)
        return
    try:
        rooms = await refinance.get_entities_by_tag(entity["id"], tag_id, active=True)
    except Exception as exc:
        await callback.answer(
            f"Could not load rooms: {str(exc)[:100]}", show_alert=True
        )
        return
    if not rooms:
        await callback.answer("No active rooms are available.", show_alert=True)
        return
    await callback.message.edit_text(
        f"🏠 <b>Choose the donation room for invoice #{invoice['id']}</b>\n\n"
        "Selecting a room does not move money. You will review and confirm next.",
        reply_markup=room_keyboard(
            int(invoice["id"]),
            rooms,
            requested_room_id or default_room_id,
            origin=origin,
            telegram_id=callback.from_user.id,
        ),
    )
    await callback.answer()


async def _show_review(
    callback: CallbackQuery,
    refinance: RefinanceClient,
    *,
    entity: dict,
    invoice: dict,
    room_id: int,
    origin: str,
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    selectable_tag_id, default_room_id = selectable_room_recipient(invoice)
    if room_id == 0 and selectable_tag_id is not None and default_room_id is None:
        await _show_room_picker(
            callback,
            refinance,
            entity=entity,
            invoice=invoice,
            requested_room_id=0,
            origin=origin,
        )
        return
    try:
        room = await _resolve_room(refinance, entity["id"], invoice, room_id)
        view = build_payment_view(invoice, room)
    except ValueError:
        await callback.answer(
            "This invoice has an incomplete recipient. Please contact an administrator.",
            show_alert=True,
        )
        return
    if room_id and room is None:
        await callback.answer("That room is no longer available.", show_alert=True)
        return
    try:
        balance = await refinance.get_balance(entity["id"])
    except Exception as exc:
        await callback.answer(
            f"Could not load balance: {str(exc)[:100]}", show_alert=True
        )
        return
    recommended_deposit = None
    if not affordable_currencies(view, balance):
        try:
            recommended_deposit = await refinance.get_recommended_deposit(entity["id"])
        except Exception:
            # Refinance may be rolling out independently. The ordinary deposit
            # dialogue remains a safe fallback when no recommendation is available.
            pass
    try:
        await callback.message.edit_text(
            format_review(view, balance),
            reply_markup=review_keyboard(
                view,
                balance,
                origin=origin,
                telegram_id=callback.from_user.id,
                recommended_deposit=recommended_deposit,
            ),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
        await callback.answer("Balance is unchanged.")
        return
    await callback.answer()


@router.callback_query(F.data.startswith("rfi:v:"))
@inject
async def invoice_review_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    parts = (callback.data or "").split(":")
    try:
        origin = parts[2]
        invoice_id = int(parts[3])
        room_id = int(parts[4])
        telegram_id = int(parts[5])
    except (IndexError, ValueError):
        await callback.answer("Invalid invoice action.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    entity, invoice = authorized
    await _show_review(
        callback,
        refinance,
        entity=entity,
        invoice=invoice,
        room_id=room_id,
        origin=origin,
    )


@router.callback_query(F.data.startswith("rfi:r:"))
@inject
async def invoice_rooms_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        origin = parts[2]
        invoice_id = int(parts[3])
        requested_room_id = int(parts[4])
        telegram_id = int(parts[5])
    except (IndexError, ValueError):
        await callback.answer("Invalid invoice action.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    entity, invoice = authorized
    await _show_room_picker(
        callback,
        refinance,
        entity=entity,
        invoice=invoice,
        requested_room_id=requested_room_id,
        origin=origin,
    )


@router.callback_query(F.data.startswith("rfi:s:"))
@inject
async def invoice_room_selected_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    parts = (callback.data or "").split(":")
    try:
        origin = parts[2]
        invoice_id = int(parts[3])
        room_id = int(parts[4])
        telegram_id = int(parts[5])
    except (IndexError, ValueError):
        await callback.answer("Invalid room selection.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    entity, invoice = authorized
    await _show_review(
        callback,
        refinance,
        entity=entity,
        invoice=invoice,
        room_id=room_id,
        origin=origin,
    )


@router.callback_query(F.data.startswith("rfi:p:"))
@inject
async def invoice_pay_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        invoice_id = int(parts[3])
        room_id = int(parts[4])
        currency = parts[5].lower()
        telegram_id = int(parts[6])
    except (IndexError, ValueError):
        await callback.answer("Invalid payment confirmation.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    entity, invoice = authorized
    try:
        room = await _resolve_room(refinance, entity["id"], invoice, room_id)
        if room_id and room is None:
            await callback.answer("That room is no longer available.", show_alert=True)
            return
        view = build_payment_view(invoice, room)
        balance = await refinance.get_balance(entity["id"])
        if currency not in affordable_currencies(view, balance):
            await callback.answer(
                "Your balance is no longer sufficient for that amount.",
                show_alert=True,
            )
            return
        if invoice.get("items"):
            await refinance.pay_invoice_items(
                entity["id"], invoice_id, payment_items(view, currency)
            )
        else:
            line = view.lines[0]
            await refinance.pay_simple_invoice(
                entity["id"], invoice, currency, str(line.amounts[currency])
            )
    except Exception as exc:
        await callback.answer(f"Payment failed: {str(exc)[:100]}", show_alert=True)
        return
    await callback.message.edit_text(format_receipt(view, currency), reply_markup=None)
    await callback.answer("Invoice paid.")


@router.callback_query(F.data.startswith("rfi:d:"))
@inject
async def invoice_deposit_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
    state: FSMContext,
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        invoice_id = int(parts[2])
        telegram_id = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Invalid deposit action.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    entity, _invoice = authorized
    try:
        recommendation = await refinance.get_recommended_deposit(entity["id"])
        amount = Decimal(str(recommendation.get("amount")))
        currency = str(recommendation.get("currency") or "").upper()
        if amount <= 0 or not currency:
            raise ValueError("No deposit recommendation")
    except Exception:
        await start_deposit_dialog(callback.message, state)
        await callback.answer()
        return
    await state.clear()
    await _do_deposit(
        callback.message,
        refinance,
        callback.from_user.id,
        callback.from_user.username,
        format(amount, "f"),
        currency,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rfi:n:"))
@inject
async def invoice_notification_back_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        invoice_id = int(parts[2])
        telegram_id = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Invalid invoice action.", show_alert=True)
        return
    authorized = await _authorized_invoice(callback, refinance, invoice_id, telegram_id)
    if authorized is None:
        return
    _, invoice = authorized
    await callback.message.edit_text(
        format_notification(invoice, is_new=False),
        reply_markup=notification_keyboard(invoice, telegram_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rfi:bal:"))
@inject
async def invoice_balance_back_callback(
    callback: CallbackQuery,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("No message context.", show_alert=True)
        return
    try:
        telegram_id = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Invalid balance action.", show_alert=True)
        return
    if callback.from_user is None or callback.from_user.id != telegram_id:
        await callback.answer("This balance belongs to another user.", show_alert=True)
        return
    try:
        entity = await refinance.get_or_link_entity(
            telegram_id, callback.from_user.username
        )
        if entity is None:
            await callback.answer("Account is not linked.", show_alert=True)
            return
        text, keyboard = await _build_balance_message(
            refinance, entity, telegram_id, False
        )
    except Exception as exc:
        await callback.answer(
            f"Could not load balance: {str(exc)[:100]}", show_alert=True
        )
        return
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


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
