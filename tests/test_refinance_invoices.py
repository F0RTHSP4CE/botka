from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings
from botka.db.models import User
from botka.handlers.refinance.callbacks import (
    invoice_pay_callback,
    invoice_review_callback,
)
from botka.handlers.refinance.commands import _build_balance_message
from botka.handlers.refinance.invoice_ui import (
    affordable_currencies,
    build_payment_view,
    format_notification,
    format_receipt,
    format_review,
    payment_items,
    review_keyboard,
)
from botka.periodic.jobs import refinance_invoices as invoice_job
from botka.periodic.jobs.base import PeriodicContext
from botka.services.refinance_client import RefinanceAPIError, RefinanceClient


def sample_invoice(*, telegram_id: int | None = 1001) -> dict:
    auth = {"telegram_id": telegram_id} if telegram_id is not None else {}
    return {
        "id": 123,
        "status": "pending",
        "billing_period": "2026-07-01",
        "created_at": "2026-07-18T10:00:00+00:00",
        "from_entity_id": 200,
        "from_entity": {"id": 200, "name": "alice", "auth": auth},
        "to_entity_id": None,
        "to_entity": None,
        "amounts": [],
        "items": [
            {
                "id": 501,
                "to_entity_id": 1,
                "to_entity": {"id": 1, "name": "F0"},
                "to_tag_id": None,
                "amounts": [
                    {"currency": "usd", "amount": "42"},
                    {"currency": "gel", "amount": "115"},
                ],
            },
            {
                "id": 502,
                "to_entity_id": 60,
                "to_entity": {"id": 60, "name": "music studio"},
                "to_tag_id": 19,
                "amounts": [
                    {"currency": "usd", "amount": "8"},
                    {"currency": "gel", "amount": "20"},
                ],
            },
        ],
    }


async def add_local_user(engine, telegram_id: int = 1001) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        session.add(User(telegram_id=telegram_id, username="alice"))
        await session.commit()


def test_refinance_api_error_contains_only_status_and_server_body() -> None:
    client = RefinanceClient.__new__(RefinanceClient)
    response = httpx.Response(
        418,
        text='{"error":"Transactions are not visible to this entity."}',
        request=httpx.Request("GET", "http://host.docker.internal:8000/transactions"),
    )

    with pytest.raises(RefinanceAPIError) as raised:
        client._raise_for_status(response)

    assert str(raised.value) == (
        'HTTP 418: {"error":"Transactions are not visible to this entity."}'
    )
    assert "host.docker.internal" not in str(raised.value)
    assert "developer.mozilla.org" not in str(raised.value)


@pytest.mark.asyncio
async def test_balance_shows_refinance_status_and_body_without_generic_prefix() -> None:
    error = RefinanceAPIError(418, '{"error":"Not allowed."}')
    refinance = SimpleNamespace(
        get_balance=AsyncMock(side_effect=error),
        get_invoices=AsyncMock(return_value=[]),
        get_transactions=AsyncMock(return_value=[]),
    )

    text, keyboard = await _build_balance_message(
        refinance, {"id": 200, "name": "alice"}, 1001
    )

    assert text == ("HTTP 418: {&quot;error&quot;:&quot;Not allowed.&quot;}")
    assert keyboard is None


def test_invoice_ui_is_explicit_and_only_offers_affordable_currencies() -> None:
    invoice = sample_invoice()
    view = build_payment_view(invoice)
    balance = {"completed": {"usd": "50", "gel": "100"}}

    notification = format_notification(invoice, is_new=False)
    assert "Pending monthly fee invoice #123" in notification
    assert "<b>monthly fee invoice #123</b>" in format_notification(
        invoice, is_new=True
    )
    assert "42 USD / 115 GEL → F0" in notification
    assert "8 USD / 20 GEL → music studio" in notification
    assert "Total:</b> 50 USD or 135 GEL" in notification
    assert "Default donation room:</b> music studio" in notification

    assert affordable_currencies(view, balance) == ["usd"]
    review = format_review(view, balance)
    assert "Choose the currency to confirm payment" in review
    keyboard = review_keyboard(view, balance, origin="n", telegram_id=1001)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "Pay 50 USD" in labels
    assert "Pay 135 GEL" not in labels


def test_room_override_changes_review_payload_and_receipt() -> None:
    invoice = sample_invoice()
    room = {"id": 58, "name": "electronics lab"}
    view = build_payment_view(invoice, room)

    assert view.selected_room_id == 58
    payload = payment_items(view, "usd")
    assert payload == [
        {
            "item_id": 501,
            "to_entity_id": 1,
            "currency": "usd",
            "amount": "42",
        },
        {
            "item_id": 502,
            "to_entity_id": 58,
            "currency": "usd",
            "amount": "8",
        },
    ]
    receipt = format_receipt(view, "usd")
    assert "8 USD → electronics lab" in receipt
    assert "Total:</b> 50 USD" in receipt


def test_review_without_sufficient_balance_has_no_confirm_button() -> None:
    view = build_payment_view(sample_invoice())
    balance = {"completed": {"eur": "0.00", "usd": "39.69", "gel": "-319.25"}}

    assert affordable_currencies(view, balance) == []
    assert format_review(view, balance) == "\n".join(
        [
            "🧾 <b>Pay invoice #123?</b>",
            "    42 USD / 115 GEL → F0",
            "    8 USD / 20 GEL → music studio",
            "    <b>Total:</b> 50 USD or 135 GEL",
            "",
            "⚠️ <b>Your balance is too low</b>",
            "    0.00 EUR",
            "    39.69 USD",
            "    -319.25 GEL",
            "",
            "Use /deposit to top up before paying this invoice.",
        ]
    )
    keyboard = review_keyboard(
        view,
        balance,
        origin="n",
        telegram_id=1001,
        recommended_deposit={"amount": "330.56", "currency": "usd"},
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["Deposit 330.56 USD", "Check balance again", "Back"]
    assert keyboard.inline_keyboard[1][0].callback_data == "rfi:v:n:123:60:1001"


def test_legacy_simple_invoice_has_clear_payment_without_room_choice() -> None:
    invoice = {
        "id": 321,
        "to_entity_id": 1,
        "to_entity": {"id": 1, "name": "F0"},
        "amounts": [
            {"currency": "usd", "amount": "42"},
            {"currency": "gel", "amount": "115"},
        ],
        "items": [],
    }
    view = build_payment_view(invoice)
    balance = {"completed": {"usd": "42"}}

    assert view.totals == {"usd": 42, "gel": 115}
    assert view.selectable_tag_id is None
    keyboard = review_keyboard(view, balance, origin="n", telegram_id=1001)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["Pay 42 USD", "Back"]


@pytest.mark.asyncio
async def test_own_balance_has_a_payment_button_for_every_pending_invoice() -> None:
    invoices = [{**sample_invoice(), "id": invoice_id} for invoice_id in range(1, 7)]
    refinance = SimpleNamespace(
        get_balance=AsyncMock(return_value={"completed": {"usd": "100"}}),
        get_invoices=AsyncMock(return_value=invoices),
        get_transactions=AsyncMock(return_value=[]),
    )
    entity = {"id": 200, "name": "alice"}

    text, keyboard = await _build_balance_message(refinance, entity, 1001)

    assert keyboard is not None
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == [f"Pay invoice #{invoice_id}" for invoice_id in range(1, 7)]
    assert "#6" in text

    _, other_keyboard = await _build_balance_message(
        refinance, entity, 1001, viewing_other=True
    )
    assert other_keyboard is None


@pytest.mark.asyncio
async def test_balance_message_has_clear_sections_without_duplicate_invoice_total() -> (
    None
):
    invoice = {**sample_invoice(), "id": 785}
    refinance = SimpleNamespace(
        get_balance=AsyncMock(
            return_value={"completed": {"usd": "39.69", "gel": "-319.25"}}
        ),
        get_invoices=AsyncMock(return_value=[invoice]),
        get_transactions=AsyncMock(
            return_value=[
                {
                    "id": 4612,
                    "from_entity": {"name": "Mike_Went"},
                    "to_entity": {"name": "Fridge"},
                    "amount": "5.00",
                    "currency": "gel",
                    "status": "completed",
                }
            ]
        ),
    )

    text, _ = await _build_balance_message(
        refinance, {"id": 200, "name": "Mike_Went"}, 1001
    )

    assert text == "\n".join(
        [
            "💰 <b>Balance</b>",
            "    39.69 USD",
            "    -319.25 GEL",
            "",
            "🧾 <b>Unpaid invoice</b>",
            "    <b>#785 · July 2026</b>",
            "    50.00 USD or 135.00 GEL",
            "    Donation room: music studio",
            "",
            "🔁 <b>Latest transaction</b>",
            "    <b>5.00 GEL</b>",
            "    Mike_Went → Fridge",
            "    #4612 · Completed",
        ]
    )
    assert text.count("50.00 USD") == 1


@pytest.mark.asyncio
async def test_balance_payment_opens_room_picker_when_default_is_missing() -> None:
    invoice = sample_invoice()
    invoice["items"][1]["to_entity_id"] = None
    invoice["items"][1]["to_entity"] = None
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    callback = SimpleNamespace(
        data="rfi:v:b:123:0:1001",
        message=message,
        from_user=SimpleNamespace(id=1001, username="alice"),
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(return_value={"id": 200, "name": "alice"}),
        get_invoice=AsyncMock(return_value=invoice),
        get_entities_by_tag=AsyncMock(
            return_value=[
                {"id": 60, "name": "music studio"},
                {"id": 58, "name": "electronics lab"},
            ]
        ),
        get_balance=AsyncMock(),
    )

    await invoice_review_callback.__dishka_orig_func__(callback, refinance)

    message.edit_text.assert_awaited_once()
    text = message.edit_text.await_args.args[0]
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    assert "Choose the donation room for invoice #123" in text
    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "music studio",
        "electronics lab",
        "Back",
    ]
    assert keyboard.inline_keyboard[-1][0].callback_data == "rfi:bal:1001"
    refinance.get_balance.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_low_balance_review_uses_refinance_recommended_deposit() -> None:
    invoice = sample_invoice()
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    callback = SimpleNamespace(
        data="rfi:v:n:123:0:1001",
        message=message,
        from_user=SimpleNamespace(id=1001, username="alice"),
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(return_value={"id": 200, "name": "alice"}),
        get_invoice=AsyncMock(return_value=invoice),
        get_balance=AsyncMock(
            return_value={"completed": {"usd": "39.69", "gel": "-319.25"}}
        ),
        get_recommended_deposit=AsyncMock(
            return_value={"entity_id": 200, "currency": "usd", "amount": "330.56"}
        ),
    )

    await invoice_review_callback.__dishka_orig_func__(callback, refinance)

    refinance.get_recommended_deposit.assert_awaited_once_with(200)
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["Deposit 330.56 USD", "Check balance again", "Back"]


@pytest.mark.asyncio
async def test_check_balance_again_reports_unchanged_without_error() -> None:
    invoice = sample_invoice()
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified",
        )
    )
    callback = SimpleNamespace(
        data="rfi:v:n:123:60:1001",
        message=message,
        from_user=SimpleNamespace(id=1001, username="alice"),
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(return_value={"id": 200, "name": "alice"}),
        get_invoice=AsyncMock(return_value=invoice),
        get_entities_by_tag=AsyncMock(
            return_value=[{"id": 60, "name": "music studio"}]
        ),
        get_balance=AsyncMock(
            return_value={"completed": {"usd": "39.69", "gel": "-319.25"}}
        ),
        get_recommended_deposit=AsyncMock(
            return_value={"entity_id": 200, "currency": "usd", "amount": "131.77"}
        ),
    )

    await invoice_review_callback.__dishka_orig_func__(callback, refinance)

    callback.answer.assert_awaited_once_with("Balance is unchanged.")


@pytest.mark.asyncio
async def test_invoice_payment_callback_rebuilds_trusted_payload() -> None:
    invoice = sample_invoice()
    invoice["items"][1]["to_entity_id"] = None
    invoice["items"][1]["to_entity"] = None
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    callback = SimpleNamespace(
        data="rfi:p:n:123:58:usd:1001",
        message=message,
        from_user=SimpleNamespace(id=1001, username="alice"),
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(
        get_or_link_entity=AsyncMock(
            return_value={"id": 200, "name": "alice", "auth": {"telegram_id": 1001}}
        ),
        get_invoice=AsyncMock(return_value=invoice),
        get_entities_by_tag=AsyncMock(
            return_value=[{"id": 58, "name": "electronics lab"}]
        ),
        get_balance=AsyncMock(return_value={"completed": {"usd": "50", "gel": "0"}}),
        pay_invoice_items=AsyncMock(return_value={**invoice, "status": "paid"}),
    )

    await invoice_pay_callback.__dishka_orig_func__(callback, refinance)

    refinance.pay_invoice_items.assert_awaited_once_with(
        200,
        123,
        [
            {
                "item_id": 501,
                "to_entity_id": 1,
                "currency": "usd",
                "amount": "42",
            },
            {
                "item_id": 502,
                "to_entity_id": 58,
                "currency": "usd",
                "amount": "8",
            },
        ],
    )
    message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_with("Invoice paid.")


@pytest.mark.asyncio
async def test_invoice_payment_callback_rejects_another_user() -> None:
    message = MagicMock(spec=Message)
    callback = SimpleNamespace(
        data="rfi:p:n:123:60:usd:1001",
        message=message,
        from_user=SimpleNamespace(id=2002, username="mallory"),
        answer=AsyncMock(),
    )
    refinance = SimpleNamespace(get_or_link_entity=AsyncMock())

    await invoice_pay_callback.__dishka_orig_func__(callback, refinance)

    refinance.get_or_link_entity.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "This invoice belongs to another user.", show_alert=True
    )


@pytest.mark.asyncio
async def test_invoice_polling_notifies_once_and_persists_receipt(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_local_user(engine)
    invoice = sample_invoice()
    fake_client = SimpleNamespace(
        is_configured=True,
        get_pending_fee_invoices=AsyncMock(return_value=[invoice]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(invoice_job, "RefinanceClient", lambda settings: fake_client)
    monkeypatch.setattr(invoice_job, "_is_new_invoice", lambda invoice, seconds: False)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=77))
    )
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="secret",
        refinance_bot_entity_id=1,
        refinance_invoice_poll_seconds=60,
    )
    context = PeriodicContext(
        bot=bot,
        settings=settings,
        sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
    )

    await invoice_job.notify_refinance_invoices(context)
    await invoice_job.notify_refinance_invoices(context)

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 1001
    assert "Pending monthly fee invoice #123" in kwargs["text"]
    assert fake_client.close.await_count == 2


@pytest.mark.asyncio
async def test_failed_invoice_notification_is_retried(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_local_user(engine)
    fake_client = SimpleNamespace(
        is_configured=True,
        get_pending_fee_invoices=AsyncMock(return_value=[sample_invoice()]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(invoice_job, "RefinanceClient", lambda settings: fake_client)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[RuntimeError("blocked"), SimpleNamespace(message_id=88)]
        )
    )
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    context = PeriodicContext(
        bot=bot,
        settings=settings,
        sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
    )

    await invoice_job.notify_refinance_invoices(context)
    await invoice_job.notify_refinance_invoices(context)

    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_invoice_polling_skips_payer_without_telegram_id(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = SimpleNamespace(
        is_configured=True,
        get_pending_fee_invoices=AsyncMock(
            return_value=[sample_invoice(telegram_id=None)]
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(invoice_job, "RefinanceClient", lambda settings: fake_client)
    bot = SimpleNamespace(send_message=AsyncMock())
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="secret",
        refinance_bot_entity_id=1,
    )
    context = PeriodicContext(
        bot=bot,
        settings=settings,
        sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
    )

    await invoice_job.notify_refinance_invoices(context)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoice_polling_skips_telegram_user_absent_from_botka(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = SimpleNamespace(
        is_configured=True,
        get_pending_fee_invoices=AsyncMock(return_value=[sample_invoice()]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(invoice_job, "RefinanceClient", lambda settings: fake_client)
    bot = SimpleNamespace(send_message=AsyncMock())
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    context = PeriodicContext(
        bot=bot,
        settings=settings,
        sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
    )

    await invoice_job.notify_refinance_invoices(context)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_refinance_client_pages_all_pending_fee_invoices() -> None:
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    client = RefinanceClient(settings)
    first_page = [{"id": index} for index in range(100)]
    client._get = AsyncMock(
        side_effect=[
            {"items": first_page},
            {"items": [{"id": 100}]},
        ]
    )

    invoices = await client.get_pending_fee_invoices()

    assert len(invoices) == 101
    assert client._get.await_args_list[0].args[2]["skip"] == 0
    assert client._get.await_args_list[1].args[2]["skip"] == 100
    assert client._get.await_args_list[0].args[2]["tags_ids"] == 3


@pytest.mark.asyncio
async def test_refinance_client_caches_active_rooms_for_twelve_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    client = RefinanceClient(settings)
    now = 1_000.0
    monkeypatch.setattr("botka.services.refinance_client.time.monotonic", lambda: now)
    client._get = AsyncMock(
        side_effect=[
            {"items": [{"id": 60, "name": "music studio"}]},
            {"items": [{"id": 58, "name": "electronics lab"}]},
        ]
    )

    first = await client.get_entities_by_tag(200, 19)
    first[0]["name"] = "mutated locally"
    now += 12 * 60 * 60 - 1
    cached = await client.get_entities_by_tag(201, 19)

    assert cached == [{"id": 60, "name": "music studio"}]
    assert client._get.await_count == 1

    now += 1
    refreshed = await client.get_entities_by_tag(201, 19)

    assert refreshed == [{"id": 58, "name": "electronics lab"}]
    assert client._get.await_count == 2


@pytest.mark.asyncio
async def test_refinance_client_uses_stale_rooms_if_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    client = RefinanceClient(settings)
    now = 1_000.0
    monkeypatch.setattr("botka.services.refinance_client.time.monotonic", lambda: now)
    client._get = AsyncMock(
        return_value={"items": [{"id": 60, "name": "music studio"}]}
    )
    await client.get_entities_by_tag(200, 19)
    now += 12 * 60 * 60
    client._get.side_effect = RuntimeError("temporary outage")

    rooms = await client.get_entities_by_tag(200, 19)
    cached_rooms = await client.get_entities_by_tag(201, 19)

    assert rooms == [{"id": 60, "name": "music studio"}]
    assert cached_rooms == rooms
    assert client._get.await_count == 2


@pytest.mark.asyncio
async def test_refinance_client_fetches_recommended_deposit_for_entity() -> None:
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        refinance_api_url="https://refinance.example",
        refinance_secret_key="test-secret-key-with-at-least-32-bytes",
        refinance_bot_entity_id=1,
    )
    client = RefinanceClient(settings)
    client._get = AsyncMock(
        return_value={"entity_id": 200, "currency": "usd", "amount": "330.56"}
    )

    recommendation = await client.get_recommended_deposit(200)

    assert recommendation["amount"] == "330.56"
    assert client._get.await_args.args[0] == "/balances/200/recommended-deposit"
