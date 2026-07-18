from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from botka.handlers.menu import cancel_kb


class DepositDialog(StatesGroup):
    waiting_amount = State()


async def start_deposit_dialog(message: Message, state: FSMContext) -> None:
    await state.set_state(DepositDialog.waiting_amount)
    await message.reply(
        "Enter the amount and currency to deposit, e.g. <code>10 GEL</code>:",
        reply_markup=cancel_kb(),
    )
