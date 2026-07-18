"""Refinance slash-command handlers.

/transfer [@username | reply] 100 GEL [comment]  → draft tx → confirm/cancel keyboard
/request  [@username | reply] 50  GEL [comment]  → draft tx → payer gets Pay/Deny keyboard
/balance  [@username]                            → balance overview
/deposit  10 GEL                                 → keepz deposit link → PM
/transactions                                    → last 10 txs → PM
"""

from __future__ import annotations

import asyncio
import html
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.menu import Btn, send_main_menu
from botka.handlers.refinance.dialogs import start_deposit_dialog
from botka.handlers.refinance.invoice_ui import build_payment_view
from botka.services.refinance_client import RefinanceClient
from botka.services.user_service import UserService

router = Router(name=__name__)

_NOT_CONFIGURED = "Refinance integration is not configured."
_NOT_LINKED = (
    "Your Telegram account is not linked to a refinance entity. "
    "Make sure your Telegram username matches your refinance entity name."
)


# ------------------------------------------------------------------ #
# Shared helpers                                                        #
# ------------------------------------------------------------------ #


def _parse_amount_currency(parts: list[str]) -> tuple[str, str] | None:
    """Parse ['100', 'USD'] → ('100', 'USD') with basic validation."""
    if len(parts) < 2:
        return None
    raw_amount, raw_currency = parts[0], parts[1].upper()
    if not raw_currency.isalpha():
        return None
    try:
        amount = Decimal(raw_amount)
        if amount <= 0:
            return None
        return str(amount), raw_currency
    except InvalidOperation:
        return None


async def _resolve_self(
    client: RefinanceClient,
    telegram_id: int,
    username: str | None,
) -> dict | None:
    try:
        return await client.get_or_link_entity(telegram_id, username)
    except Exception:
        return None


async def _resolve_target_by_username(
    client: RefinanceClient,
    user_service: UserService,
    raw: str,
) -> dict | None:
    """Resolve a '@username' string to a refinance entity.

    First tries via botka's local user DB (telegram_id lookup).
    Falls back to a direct entity name lookup in refinance so that users
    who have never interacted with the bot can still be found.
    """
    username = raw.lstrip("@")
    bot_user = await user_service.get_user_by_username(username)
    if bot_user is not None:
        try:
            entity = await client.get_or_link_entity(bot_user.telegram_id, username)
            if entity:
                return entity
        except Exception:
            pass
    # Fallback: look up directly by entity name in refinance
    try:
        return await client.find_entity_by_name(username)
    except Exception:
        return None


async def _resolve_target_by_telegram_id(
    client: RefinanceClient,
    telegram_id: int,
    username: str | None,
) -> dict | None:
    try:
        return await client.get_or_link_entity(telegram_id, username)
    except Exception:
        return None


def _split_args(
    message: Message, command: CommandObject
) -> tuple[str | None, list[str]]:
    """Return (raw_username_or_None, remaining_tokens).

    If the first token starts with '@' it is treated as the target username.
    If there is no '@' token the message reply (if any) supplies the target
    and all tokens become the payload (amount currency [comment]).
    """
    tokens = (command.args or "").split()
    if tokens and tokens[0].startswith("@"):
        return tokens[0], tokens[1:]
    return None, tokens


def _format_money(value: object) -> str:
    return format(Decimal(str(value)), ".2f")


def _format_balance_lines(balances: dict) -> list[str]:
    lines = [
        f"    {_format_money(value)} {currency.upper()}"
        for currency, value in balances.items()
        if Decimal(str(value)) != 0
    ]
    return lines or ["    0"]


def _billing_period_label(raw_period: object) -> str | None:
    if not raw_period:
        return None
    try:
        return date.fromisoformat(str(raw_period)[:10]).strftime("%B %Y")
    except ValueError:
        return str(raw_period)


# ------------------------------------------------------------------ #
# /transfer                                                             #
# ------------------------------------------------------------------ #


@router.message(Command("transfer"))
@inject
async def transfer_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    username_arg, rest = _split_args(message, command)
    reply_user = (
        message.reply_to_message.from_user
        if message.reply_to_message
        and message.reply_to_message.from_user
        and not message.reply_to_message.forum_topic_created
        else None
    )

    if username_arg is None and reply_user is None:
        await message.reply(
            "Usage: <code>/transfer @username 100 USD [comment]</code> "
            "or reply to a user's message."
        )
        return

    parsed = _parse_amount_currency(rest)
    if parsed is None or len(rest) < 2:
        await message.reply("Usage: <code>/transfer @username 100 GEL [comment]</code>")
        return
    amount, currency = parsed
    comment = " ".join(rest[2:]) or None

    if username_arg:
        actor_entity, target_entity = await asyncio.gather(
            _resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_username(refinance, user_service, username_arg),
        )
        target_label = username_arg
    else:
        actor_entity, target_entity = await asyncio.gather(
            _resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_telegram_id(
                refinance, reply_user.id, reply_user.username  # type: ignore[union-attr]
            ),
        )
        target_label = (
            f"@{reply_user.username}" if reply_user.username else str(reply_user.id)  # type: ignore[union-attr]
        )

    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return
    if target_entity is None:
        await message.reply(f"User {html.escape(target_label)} not found in refinance.")
        return

    actor_entity_id = actor_entity["id"]
    target_entity_id = target_entity["id"]
    actor_telegram_id = message.from_user.id

    try:
        tx = await refinance.create_transaction(
            actor_entity_id=actor_entity_id,
            from_entity_id=actor_entity_id,
            to_entity_id=target_entity_id,
            amount=amount,
            currency=currency,
            status="draft",
            comment=comment,
        )
    except Exception as exc:
        await message.reply(f"Failed to create transfer: {html.escape(str(exc))}")
        return

    tx_id: int = tx["id"]
    target_name = html.escape(target_entity["name"])
    # @mention so the target gets a Telegram notification
    target_mention = target_label if target_label.startswith("@") else target_name

    body = (
        f"{target_mention} \u2014 <b>{html.escape(actor_entity['name'])}</b> wants to "
        f"transfer <b>{html.escape(amount)} {html.escape(currency)}</b> to you "
        f"(tx #{tx_id})."
    )
    api_comment = tx.get("comment") or comment
    if api_comment:
        body += f"\nComment: <i>{html.escape(api_comment)}</i>"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Confirm: {amount} {currency} → {target_name}",
                    callback_data=f"rf_tx:confirm:{tx_id}:{actor_entity_id}:{actor_telegram_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"rf_tx:cancel:{tx_id}:{actor_entity_id}:{actor_telegram_id}",
                ),
            ]
        ]
    )
    await message.reply(body, reply_markup=keyboard)


# ------------------------------------------------------------------ #
# /request                                                              #
# ------------------------------------------------------------------ #


@router.message(Command("request"))
@inject
async def request_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    username_arg, rest = _split_args(message, command)
    reply_user = (
        message.reply_to_message.from_user
        if message.reply_to_message
        and message.reply_to_message.from_user
        and not message.reply_to_message.forum_topic_created
        else None
    )

    if username_arg is None and reply_user is None:
        await message.reply(
            "Usage: <code>/request @username 50 USD [comment]</code> "
            "or reply to a user's message."
        )
        return

    parsed = _parse_amount_currency(rest)
    if parsed is None or len(rest) < 2:
        await message.reply("Usage: <code>/request @username 50 GEL [comment]</code>")
        return
    amount, currency = parsed
    comment = " ".join(rest[2:]) or None

    if username_arg:
        actor_entity, payer_entity = await asyncio.gather(
            _resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_username(refinance, user_service, username_arg),
        )
        payer_label = username_arg
    else:
        actor_entity, payer_entity = await asyncio.gather(
            _resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_telegram_id(
                refinance, reply_user.id, reply_user.username  # type: ignore[union-attr]
            ),
        )
        payer_label = (
            f"@{reply_user.username}" if reply_user.username else str(reply_user.id)  # type: ignore[union-attr]
        )

    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return
    if payer_entity is None:
        await message.reply(f"User {html.escape(payer_label)} not found in refinance.")
        return

    # Create a draft transaction: payer → requester (status=draft)
    try:
        tx = await refinance.create_transaction(
            actor_entity_id=actor_entity["id"],
            from_entity_id=payer_entity["id"],
            to_entity_id=actor_entity["id"],
            amount=amount,
            currency=currency,
            status="draft",
            comment=comment,
        )
    except Exception as exc:
        await message.reply(f"Failed to create request: {html.escape(str(exc))}")
        return

    tx_id: int = tx["id"]
    payer_entity_id: int = payer_entity["id"]
    requester_entity_id: int = actor_entity["id"]
    requester_telegram_id: int = message.from_user.id

    # Resolve payer's telegram_id for authorization on the callback.
    payer_telegram_id: int | None = None
    if reply_user is not None:
        payer_telegram_id = reply_user.id
    else:
        raw_tid = (payer_entity.get("auth") or {}).get("telegram_id")
        if raw_tid not in (None, ""):
            try:
                payer_telegram_id = int(raw_tid)
            except (ValueError, TypeError):
                pass

    payer_tid_field = payer_telegram_id or 0

    # @mention so the payer gets a Telegram notification
    payer_mention = (
        payer_label
        if payer_label.startswith("@")
        else html.escape(payer_entity["name"])
    )

    body = (
        f"{payer_mention} \u2014 <b>{html.escape(actor_entity['name'])}</b> requests "
        f"<b>{html.escape(amount)} {html.escape(currency)}</b> from you "
        f"(tx #{tx_id})."
    )
    api_comment = tx.get("comment") or comment
    if api_comment:
        body += f"\nComment: <i>{html.escape(api_comment)}</i>"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Pay {amount} {currency}",
                    callback_data=f"rf_req:confirm:{tx_id}:{payer_entity_id}:{payer_tid_field}",
                ),
                InlineKeyboardButton(
                    text="❌ Deny",
                    callback_data=f"rf_req:cancel:{tx_id}:{payer_entity_id}:{payer_tid_field}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel request",
                    callback_data=f"rf_req:author_cancel:{tx_id}:{requester_entity_id}:{requester_telegram_id}",
                ),
            ],
        ]
    )
    await message.reply(body, reply_markup=keyboard)


# ------------------------------------------------------------------ #
# /balance                                                              #
# ------------------------------------------------------------------ #


async def _build_balance_message(
    refinance: RefinanceClient,
    entity: dict,
    telegram_id: int,
    viewing_other: bool = False,
) -> tuple[str, InlineKeyboardMarkup | None]:
    entity_id = entity["id"]
    try:
        balance, pending_invoices, last_txs = await asyncio.gather(
            refinance.get_balance(entity_id),
            refinance.get_invoices(entity_id, status="pending", limit=20),
            refinance.get_transactions(entity_id, limit=1),
        )
    except Exception as exc:
        return html.escape(str(exc)), None

    lines: list[str] = []
    if viewing_other:
        lines.extend([f"👤 <b>{html.escape(entity['name'])}</b>", ""])

    completed = balance.get("completed") or {}
    lines.append("💰 <b>Balance</b>")
    lines.extend(_format_balance_lines(completed))

    draft = balance.get("draft") or {}
    draft_lines = _format_balance_lines(draft)
    if draft_lines != ["    0"]:
        lines.extend(["", "📋 <b>Draft balance</b>", *draft_lines])

    if pending_invoices:
        invoice_count = len(pending_invoices)
        invoice_heading = (
            "🧾 <b>Unpaid invoice</b>"
            if invoice_count == 1
            else f"🧾 <b>Unpaid invoices · {invoice_count}</b>"
        )
        lines.extend(["", invoice_heading])
        for invoice_index, inv in enumerate(pending_invoices):
            try:
                view = build_payment_view(inv)
                amounts_str = " or ".join(
                    f"{_format_money(amount)} {currency.upper()}"
                    for currency, amount in view.totals.items()
                )
                room_name = view.selected_room_name
            except ValueError:
                amounts_str = "Choose a donation room to review the amount"
                room_name = None
            period = _billing_period_label(inv.get("billing_period"))
            invoice_title = f"    <b>#{inv['id']}"
            if period:
                invoice_title += f" · {html.escape(period)}"
            invoice_title += "</b>"
            if invoice_index > 0:
                lines.append("")
            lines.extend([invoice_title, f"    {amounts_str}"])
            if room_name:
                lines.append(f"    Donation room: {html.escape(room_name)}")
    else:
        lines.extend(["", "🧾 <b>Invoices</b>", "    No unpaid invoices."])

    if last_txs:
        tx = last_txs[0]
        from_name = html.escape((tx.get("from_entity") or {}).get("name", "?"))
        to_name = html.escape((tx.get("to_entity") or {}).get("name", "?"))
        status = str(tx.get("status") or "unknown").replace("_", " ").capitalize()
        lines.extend(
            [
                "",
                "🔁 <b>Latest transaction</b>",
                f"    <b>{_format_money(tx['amount'])} {tx['currency'].upper()}</b>",
                f"    {from_name} → {to_name}",
                f"    #{tx['id']} · {html.escape(status)}",
            ]
        )

    keyboard = None
    if not viewing_other and pending_invoices:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Pay invoice #{invoice['id']}",
                        callback_data=f"rfi:v:b:{invoice['id']}:0:{telegram_id}",
                    )
                ]
                for invoice in pending_invoices
            ]
        )
    return "\n".join(lines), keyboard


async def _do_balance_for_entity(
    message: Message,
    refinance: RefinanceClient,
    entity: dict,
    viewing_other: bool = False,
) -> None:
    telegram_id = message.from_user.id if message.from_user else 0
    text, keyboard = await _build_balance_message(
        refinance, entity, telegram_id, viewing_other
    )
    await message.reply(text, reply_markup=keyboard)


@router.message(Command("balance"))
@inject
async def balance_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    args = (command.args or "").split()
    viewing_other = False

    if args and args[0].startswith("@"):
        entity = await _resolve_target_by_username(refinance, user_service, args[0])
        if entity is None:
            await message.reply(f"User {html.escape(args[0])} not found in refinance.")
            return
        viewing_other = True
    else:
        entity = await _resolve_self(
            refinance, message.from_user.id, message.from_user.username
        )
        if entity is None:
            await message.reply(_NOT_LINKED)
            return

    await _do_balance_for_entity(message, refinance, entity, viewing_other)


@router.message(F.text == Btn.BALANCE, F.chat.type == "private")
@inject
async def menu_balance_message(
    message: Message,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    entity = await _resolve_self(
        refinance, message.from_user.id, message.from_user.username
    )
    if entity is None:
        await message.reply(_NOT_LINKED)
        return
    await _do_balance_for_entity(message, refinance, entity)


# ------------------------------------------------------------------ #
# /deposit                                                              #
# ------------------------------------------------------------------ #


async def _do_deposit(
    message: Message,
    refinance: RefinanceClient,
    sender_id: int,
    sender_username: str | None,
    amount: str,
    currency: str,
) -> None:
    """Create a Keepz deposit and reply with the payment link."""
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    entity = await _resolve_self(refinance, sender_id, sender_username)
    if entity is None:
        await message.reply(_NOT_LINKED)
        return
    try:
        deposit = await refinance.create_keepz_deposit(
            entity_id=entity["id"],
            amount=amount,
            currency=currency,
        )
    except Exception as exc:
        await message.reply(f"Failed to create deposit: {html.escape(str(exc))}")
        return
    details = (deposit.get("details") or {}).get("keepz") or {}
    payment_url = details.get("payment_url") or details.get("payment_short_url")
    text = f"💳 Deposit <b>{html.escape(amount)} {html.escape(currency)}</b> created."
    if not payment_url:
        text += "\nPayment link not available yet."
    deposit_id = deposit.get("id")
    check_kb = None
    if deposit_id is not None:
        rows = []
        if payment_url:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"💳 Pay {html.escape(amount)} {html.escape(currency)} via Keepz",
                        url=payment_url,
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Check payment",
                    callback_data=f"rf_dep:check:{deposit_id}:{entity['id']}:{sender_id}",
                )
            ]
        )
        check_kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if message.chat.type != "private":
        try:
            await message.bot.send_message(
                chat_id=sender_id,
                text=text,
                disable_web_page_preview=False,
                reply_markup=check_kb,
            )
            await message.reply("💳 Deposit link sent to your private messages.")
        except Exception:
            await message.reply(
                text, disable_web_page_preview=False, reply_markup=check_kb
            )
    else:
        await message.reply(text, disable_web_page_preview=False, reply_markup=check_kb)


async def _do_transfer_draft(
    message: Message,
    refinance: RefinanceClient,
    user_service: UserService,
    sender_id: int,
    sender_username: str | None,
    target_username: str,
    amount: str,
    currency: str,
    comment: str | None = None,
) -> None:
    """Create a draft transfer transaction and reply with confirm/cancel keyboard."""
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    actor_entity, target_entity = await asyncio.gather(
        _resolve_self(refinance, sender_id, sender_username),
        _resolve_target_by_username(refinance, user_service, target_username),
    )
    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return
    if target_entity is None:
        await message.reply(
            f"User {html.escape(target_username)} not found in refinance."
        )
        return
    actor_entity_id = actor_entity["id"]
    target_entity_id = target_entity["id"]
    try:
        tx = await refinance.create_transaction(
            actor_entity_id=actor_entity_id,
            from_entity_id=actor_entity_id,
            to_entity_id=target_entity_id,
            amount=amount,
            currency=currency,
            status="draft",
            comment=comment,
        )
    except Exception as exc:
        await message.reply(f"Failed to create transfer: {html.escape(str(exc))}")
        return
    tx_id: int = tx["id"]
    target_name = html.escape(target_entity["name"])
    target_mention = target_username if target_username.startswith("@") else target_name
    body = (
        f"{target_mention} — <b>{html.escape(actor_entity['name'])}</b> wants to "
        f"transfer <b>{html.escape(amount)} {html.escape(currency)}</b> to you "
        f"(tx #{tx_id})."
    )
    api_comment = tx.get("comment") or comment
    if api_comment:
        body += f"\nComment: <i>{html.escape(api_comment)}</i>"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Confirm: {amount} {currency} → {target_name}",
                    callback_data=f"rf_tx:confirm:{tx_id}:{actor_entity_id}:{sender_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"rf_tx:cancel:{tx_id}:{actor_entity_id}:{sender_id}",
                ),
            ]
        ]
    )
    await message.reply(body, reply_markup=keyboard)


@router.message(Command("deposit"))
@inject
async def deposit_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    state: FSMContext,
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    args = (command.args or "").split()
    if not args and message.chat.type == "private":
        await start_deposit_dialog(message, state)
        return
    if len(args) < 2:
        await message.reply("Usage: <code>/deposit 10 GEL</code>")
        return
    parsed = _parse_amount_currency(args)
    if parsed is None:
        await message.reply("Invalid amount or currency.")
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


# ------------------------------------------------------------------ #
# /transactions                                                         #
# ------------------------------------------------------------------ #


async def _do_transactions(
    message: Message,
    refinance: RefinanceClient,
    sender_id: int,
    sender_username: str | None,
) -> None:
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return
    entity = await _resolve_self(refinance, sender_id, sender_username)
    if entity is None:
        await message.reply(_NOT_LINKED)
        return
    try:
        txs = await refinance.get_transactions(entity["id"], limit=10)
    except Exception as exc:
        await message.reply(f"Error: {html.escape(str(exc))}")
        return
    if not txs:
        await message.reply("No transactions yet.")
        return
    lines = ["<b>Last transactions:</b>"]
    for tx in txs:
        from_name = html.escape((tx.get("from_entity") or {}).get("name", "?"))
        to_name = html.escape((tx.get("to_entity") or {}).get("name", "?"))
        status_emoji = "✅" if tx["status"] == "completed" else "📋"
        lines.append(
            f"{status_emoji} #{tx['id']} {from_name} → {to_name}: "
            f"{tx['amount']} {tx['currency'].upper()}"
        )
    text = "\n".join(lines)
    if message.chat.type != "private":
        try:
            await message.bot.send_message(chat_id=sender_id, text=text)
            await message.reply("📊 Transaction history sent to your private messages.")
        except Exception:
            await message.reply(text)
    else:
        await message.reply(text)


@router.message(Command("transactions"))
@inject
async def transactions_handler(
    message: Message,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    await _do_transactions(
        message, refinance, message.from_user.id, message.from_user.username
    )


@router.message(F.text == Btn.TRANSACTIONS, F.chat.type == "private")
@inject
async def menu_transactions_message(
    message: Message,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        return
    await _do_transactions(
        message, refinance, message.from_user.id, message.from_user.username
    )
