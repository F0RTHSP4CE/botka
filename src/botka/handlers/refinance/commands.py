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
from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

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


def _format_balance_dict(bal: dict) -> str:
    parts = [f"{v} {k.upper()}" for k, v in bal.items() if Decimal(str(v)) != 0]
    return ", ".join(parts) if parts else "0"


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

    entity_id = entity["id"]
    try:
        balance, pending_invoices, last_txs = await asyncio.gather(
            refinance.get_balance(entity_id),
            refinance.get_invoices(entity_id, status="pending", limit=20),
            refinance.get_transactions(entity_id, limit=1),
        )
    except Exception as exc:
        await message.reply(f"Error fetching data: {html.escape(str(exc))}")
        return

    lines: list[str] = []
    if viewing_other:
        lines.append(f"Balance for <b>{html.escape(entity['name'])}</b>")

    completed = balance.get("completed") or {}
    bal_str = _format_balance_dict(completed)
    lines.append(f"💰 <b>Balance:</b> {bal_str}")

    draft = balance.get("draft") or {}
    draft_str = _format_balance_dict(draft)
    if draft_str != "0":
        lines.append(f"📋 <b>Draft:</b> {draft_str}")

    if pending_invoices:
        by_currency: dict[str, Decimal] = {}
        for inv in pending_invoices:
            for amt in inv.get("amounts", []):
                cur = amt["currency"].upper()
                by_currency[cur] = by_currency.get(cur, Decimal(0)) + Decimal(
                    str(amt["amount"])
                )
        inv_sum = " or ".join(f"{v} {k}" for k, v in by_currency.items())
        lines.append(f"🧾 <b>Unpaid invoices ({len(pending_invoices)}):</b> {inv_sum}")
        for inv in pending_invoices[:5]:
            from_name = html.escape((inv.get("from_entity") or {}).get("name", "?"))
            to_name = html.escape((inv.get("to_entity") or {}).get("name", "?"))
            amounts_str = " or ".join(
                f"{a['amount']} {a['currency'].upper()}" for a in inv.get("amounts", [])
            )
            lines.append(f"  · #{inv['id']} {from_name} → {to_name}: {amounts_str}")
        if len(pending_invoices) > 5:
            lines.append(f"  … and {len(pending_invoices) - 5} more")
    else:
        lines.append("🧾 No unpaid invoices.")

    if last_txs:
        tx = last_txs[0]
        from_name = html.escape((tx.get("from_entity") or {}).get("name", "?"))
        to_name = html.escape((tx.get("to_entity") or {}).get("name", "?"))
        lines.append(
            f"🔁 <b>Last tx:</b> #{tx['id']} {from_name} → {to_name}: "
            f"{tx['amount']} {tx['currency'].upper()} [{tx['status']}]"
        )

    await message.reply("\n".join(lines))


# ------------------------------------------------------------------ #
# /deposit                                                              #
# ------------------------------------------------------------------ #


@router.message(Command("deposit"))
@inject
async def deposit_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    args = (command.args or "").split()
    if len(args) < 2:
        await message.reply("Usage: <code>/deposit 10 GEL</code>")
        return

    parsed = _parse_amount_currency(args)
    if parsed is None:
        await message.reply("Invalid amount or currency.")
        return
    amount, currency = parsed

    entity = await _resolve_self(
        refinance, message.from_user.id, message.from_user.username
    )
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
                    callback_data=f"rf_dep:check:{deposit_id}:{entity['id']}:{message.from_user.id}",
                )
            ]
        )
        check_kb = InlineKeyboardMarkup(inline_keyboard=rows)

    if message.chat.type != "private":
        try:
            await message.bot.send_message(
                chat_id=message.from_user.id,
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


# ------------------------------------------------------------------ #
# /transactions                                                         #
# ------------------------------------------------------------------ #


@router.message(Command("transactions"))
@inject
async def transactions_handler(
    message: Message,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        await message.reply("Cannot determine sender.")
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
            await message.bot.send_message(
                chat_id=message.from_user.id,
                text=text,
            )
            await message.reply("📊 Transaction history sent to your private messages.")
        except Exception:
            await message.reply(text)
    else:
        await message.reply(text)
