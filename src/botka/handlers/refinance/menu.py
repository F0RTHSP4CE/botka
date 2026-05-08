"""Menu FSM dialogs for refinance: Deposit and Transfer."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User
from botka.handlers.menu import Btn, cancel_kb, send_main_menu
from botka.handlers.refinance.commands import (
    _do_deposit,
    _do_transfer_draft,
    _parse_amount_currency,
)
from botka.services.refinance_client import RefinanceClient
from botka.services.user_service import UserService

router = Router(name=__name__)
router.message.filter(F.chat.type == "private")


class DepositDialog(StatesGroup):
    waiting_amount = State()


class TransferDialog(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()


# ── Deposit ──────────────────────────────────────────────────────────


@router.message(F.text == Btn.DEPOSIT)
@inject
async def menu_deposit_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(DepositDialog.waiting_amount)
    await message.reply(
        "Enter the amount and currency to deposit, e.g. <code>10 GEL</code>:",
        reply_markup=cancel_kb(),
    )


@router.message(DepositDialog.waiting_amount, F.text != Btn.CANCEL)
@inject
async def deposit_amount_handler(
    message: Message,
    refinance: FromDishka[RefinanceClient],
    state: FSMContext,
) -> None:
    await state.clear()
    if message.from_user is None or not message.text:
        return
    parts = message.text.strip().split()
    parsed = _parse_amount_currency(parts)
    if parsed is None:
        await message.reply(
            "Invalid format. Please use e.g. <code>10 GEL</code>. "
            "Tap /menu and try again."
        )
        return
    amount, currency = parsed
    await _do_deposit(
        message,
        refinance,
        message.from_user.id,
        message.from_user.username,
        amount,
        currency,
    )
    await send_main_menu(message)


# ── Transfer ─────────────────────────────────────────────────────────


@router.message(F.text == Btn.TRANSFER)
@inject
async def menu_transfer_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(TransferDialog.waiting_recipient)
    await message.reply(
        "Who do you want to transfer to? Enter their @username:",
        reply_markup=cancel_kb(),
    )


@router.message(TransferDialog.waiting_recipient, F.text != Btn.CANCEL)
@inject
async def transfer_recipient_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.text:
        return
    recipient = message.text.strip().lstrip("@")
    if not recipient:
        await message.reply("Please enter a valid @username.", reply_markup=cancel_kb())
        return
    await state.update_data(recipient=recipient)
    await state.set_state(TransferDialog.waiting_amount)
    await message.reply(
        f"How much to send to @{recipient}? Enter amount and currency, "
        "e.g. <code>100 GEL</code>:",
        reply_markup=cancel_kb(),
    )


@router.message(TransferDialog.waiting_amount, F.text != Btn.CANCEL)
@inject
async def transfer_amount_handler(
    message: Message,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
    state: FSMContext,
    user_record: User | None = None,
) -> None:
    data = await state.get_data()
    await state.clear()
    if message.from_user is None or not message.text:
        return
    recipient: str = data.get("recipient", "")
    if not recipient:
        await message.reply("Something went wrong. Please start over with /menu.")
        return
    parts = message.text.strip().split()
    parsed = _parse_amount_currency(parts)
    if parsed is None:
        await message.reply(
            "Invalid format. Please use e.g. <code>100 GEL</code>. "
            "Tap /menu and try again."
        )
        return
    amount, currency = parsed
    await _do_transfer_draft(
        message,
        refinance,
        user_service,
        message.from_user.id,
        message.from_user.username,
        f"@{recipient}",
        amount,
        currency,
    )
    await send_main_menu(message)
