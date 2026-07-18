from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from botka.handlers.refinance.callbacks import invoice_deposit_callback
from botka.handlers.refinance.commands import deposit_handler
from botka.handlers.refinance.dialogs import DepositDialog


def _message(chat_type: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.from_user = SimpleNamespace(id=1001, username="alice")
    message.chat = SimpleNamespace(type=chat_type)
    message.reply = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_private_deposit_without_arguments_starts_amount_dialog() -> None:
    message = _message("private")
    state = SimpleNamespace(set_state=AsyncMock())
    refinance = SimpleNamespace(is_configured=True)

    await deposit_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args=None),
        refinance,
        state,
    )

    state.set_state.assert_awaited_once_with(DepositDialog.waiting_amount)
    message.reply.assert_awaited_once()
    assert "Enter the amount and currency" in message.reply.await_args.args[0]
    assert message.reply.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_group_deposit_without_arguments_keeps_compact_usage_hint() -> None:
    message = _message("group")
    state = SimpleNamespace(set_state=AsyncMock())
    refinance = SimpleNamespace(is_configured=True)

    await deposit_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args=None),
        refinance,
        state,
    )

    state.set_state.assert_not_awaited()
    message.reply.assert_awaited_once_with("Usage: <code>/deposit 10 GEL</code>")


@pytest.mark.asyncio
async def test_invoice_deposit_button_creates_recommended_deposit_for_owner() -> None:
    message = _message("private")
    callback = SimpleNamespace(
        data="rfi:d:785:1001",
        message=message,
        from_user=message.from_user,
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(return_value={"id": 200, "name": "alice"}),
        get_invoice=AsyncMock(
            return_value={
                "id": 785,
                "status": "pending",
                "from_entity_id": 200,
                "from_entity": {"auth": {"telegram_id": 1001}},
            }
        ),
        get_recommended_deposit=AsyncMock(
            return_value={"entity_id": 200, "currency": "usd", "amount": "330.56"}
        ),
        create_keepz_deposit=AsyncMock(
            return_value={
                "id": 91,
                "details": {"keepz": {"payment_url": "https://pay.example/91"}},
            }
        ),
        is_configured=True,
    )
    state = SimpleNamespace(set_state=AsyncMock(), clear=AsyncMock())

    await invoice_deposit_callback.__dishka_orig_func__(
        callback,
        refinance,
        state,
    )

    refinance.get_recommended_deposit.assert_awaited_once_with(200)
    refinance.create_keepz_deposit.assert_awaited_once_with(
        entity_id=200,
        amount="330.56",
        currency="USD",
    )
    state.clear.assert_awaited_once_with()
    state.set_state.assert_not_awaited()
    assert "Deposit <b>330.56 USD</b> created" in message.reply.await_args.args[0]
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_invoice_deposit_falls_back_to_amount_dialog_without_recommendation() -> (
    None
):
    message = _message("private")
    callback = SimpleNamespace(
        data="rfi:d:785:1001",
        message=message,
        from_user=message.from_user,
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(return_value={"id": 200, "name": "alice"}),
        get_invoice=AsyncMock(
            return_value={
                "id": 785,
                "status": "pending",
                "from_entity_id": 200,
                "from_entity": {"auth": {"telegram_id": 1001}},
            }
        ),
        get_recommended_deposit=AsyncMock(
            return_value={"entity_id": 200, "currency": None, "amount": None}
        ),
    )
    state = SimpleNamespace(set_state=AsyncMock(), clear=AsyncMock())

    await invoice_deposit_callback.__dishka_orig_func__(
        callback,
        refinance,
        state,
    )

    state.set_state.assert_awaited_once_with(DepositDialog.waiting_amount)
    state.clear.assert_not_awaited()
    assert "Enter the amount and currency" in message.reply.await_args.args[0]
