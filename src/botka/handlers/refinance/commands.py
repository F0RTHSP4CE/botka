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

from botka.handlers.refinance.shared import (
    format_split_card,
    parse_split_id,
    resolve_self,
    split_keyboard,
)
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


def _split_args(message: Message, command: CommandObject) -> tuple[str | None, list[str]]:
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
    parts = [
        f"{v} {k.upper()}"
        for k, v in bal.items()
        if Decimal(str(v)) != 0
    ]
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
        await message.reply(
            "Usage: <code>/transfer @username 100 GEL [comment]</code>"
        )
        return
    amount, currency = parsed
    comment = " ".join(rest[2:]) or None

    if username_arg:
        actor_entity, target_entity = await asyncio.gather(
            resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_username(refinance, user_service, username_arg),
        )
        target_label = username_arg
    else:
        actor_entity, target_entity = await asyncio.gather(
            resolve_self(refinance, message.from_user.id, message.from_user.username),
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
        await message.reply(
            f"User {html.escape(target_label)} not found in refinance."
        )
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
        await message.reply(
            "Usage: <code>/request @username 50 GEL [comment]</code>"
        )
        return
    amount, currency = parsed
    comment = " ".join(rest[2:]) or None

    if username_arg:
        actor_entity, payer_entity = await asyncio.gather(
            resolve_self(refinance, message.from_user.id, message.from_user.username),
            _resolve_target_by_username(refinance, user_service, username_arg),
        )
        payer_label = username_arg
    else:
        actor_entity, payer_entity = await asyncio.gather(
            resolve_self(refinance, message.from_user.id, message.from_user.username),
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
        await message.reply(
            f"User {html.escape(payer_label)} not found in refinance."
        )
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
    payer_mention = payer_label if payer_label.startswith("@") else html.escape(payer_entity["name"])

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
            await message.reply(
                f"User {html.escape(args[0])} not found in refinance."
            )
            return
        viewing_other = True
    else:
        entity = await resolve_self(
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
        inv_sum = ", ".join(f"{v} {k}" for k, v in by_currency.items())
        lines.append(
            f"🧾 <b>Unpaid invoices ({len(pending_invoices)}):</b> {inv_sum}"
        )
        for inv in pending_invoices[:5]:
            from_name = html.escape(
                (inv.get("from_entity") or {}).get("name", "?")
            )
            to_name = html.escape((inv.get("to_entity") or {}).get("name", "?"))
            amounts_str = ", ".join(
                f"{a['amount']} {a['currency'].upper()}"
                for a in inv.get("amounts", [])
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

    entity = await resolve_self(
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
            rows.append([
                InlineKeyboardButton(
                    text=f"💳 Pay {html.escape(amount)} {html.escape(currency)} via Keepz",
                    url=payment_url,
                )
            ])
        rows.append([
            InlineKeyboardButton(
                text="🔄 Check payment",
                callback_data=f"rf_dep:check:{deposit_id}:{entity['id']}:{message.from_user.id}",
            )
        ])
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
            await message.reply(text, disable_web_page_preview=False, reply_markup=check_kb)
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

    entity = await resolve_self(
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


# ------------------------------------------------------------------ #
# Split helpers                                                         #
# ------------------------------------------------------------------ #


def _parse_amount(raw: str) -> str | None:
    try:
        v = Decimal(raw)
        return str(v) if v > 0 else None
    except InvalidOperation:
        return None


async def _refresh_split_card(message: Message, split: dict) -> None:
    """Edit the replied-to split card, or post a new one if no reply."""
    if message.reply_to_message:
        await message.reply_to_message.edit_text(
            format_split_card(split),
            reply_markup=split_keyboard(split),
        )
    else:
        await message.reply(
            format_split_card(split),
            reply_markup=split_keyboard(split),
        )


# ------------------------------------------------------------------ #
# /split                                                                #
# ------------------------------------------------------------------ #


@router.message(Command("split"))
@inject
async def split_create_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    tokens = (command.args or "").split()
    # /split <amount> <currency> @recipient [comment...]
    if len(tokens) < 3 or not tokens[2].startswith("@"):
        await message.reply(
            "Usage: <code>/split &lt;amount&gt; &lt;currency&gt; @recipient [comment]</code>"
        )
        return

    amount = _parse_amount(tokens[0])
    if amount is None:
        await message.reply("Invalid amount.")
        return
    currency = tokens[1].upper()
    if not currency.isalpha():
        await message.reply("Invalid currency.")
        return
    recipient_raw = tokens[2]
    comment = " ".join(tokens[3:]) or None

    actor_entity = await resolve_self(refinance, message.from_user.id, message.from_user.username)
    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return

    recipient_entity = await _resolve_target_by_username(refinance, user_service, recipient_raw)
    if recipient_entity is None:
        await message.reply(f"Recipient {html.escape(recipient_raw)} not found in refinance.")
        return

    try:
        split = await refinance.create_split(
            actor_entity_id=actor_entity["id"],
            recipient_entity_id=recipient_entity["id"],
            amount=amount,
            currency=currency,
            comment=comment,
        )
    except Exception as exc:
        await message.reply(f"Failed to create split: {html.escape(str(exc))}")
        return

    await message.reply(
        format_split_card(split),
        reply_markup=split_keyboard(split),
    )


# ------------------------------------------------------------------ #
# /split_join                                                           #
# ------------------------------------------------------------------ #


@router.message(Command("split_join"))
@inject
async def split_join_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    tokens = (command.args or "").split()
    split_id = parse_split_id(tokens, message)
    if split_id is None:
        await message.reply(
            "Usage: <code>/split_join &lt;id&gt; [amount]</code> or reply to a split card."
        )
        return

    amount_tokens = tokens[1:] if tokens and tokens[0].isdigit() else tokens
    fixed_amount: str | None = None
    if amount_tokens:
        fixed_amount = _parse_amount(amount_tokens[0])
        if fixed_amount is None:
            await message.reply("Invalid amount.")
            return

    entity = await resolve_self(refinance, message.from_user.id, message.from_user.username)
    if entity is None:
        await message.reply(_NOT_LINKED)
        return

    try:
        split = await refinance.upsert_split_participant(
            actor_entity_id=entity["id"],
            split_id=split_id,
            entity_id=entity["id"],
            fixed_amount=fixed_amount,
        )
    except Exception as exc:
        await message.reply(f"Failed to join split: {html.escape(str(exc))}")
        return

    await _refresh_split_card(message, split)


# ------------------------------------------------------------------ #
# /split_add                                                            #
# ------------------------------------------------------------------ #


@router.message(Command("split_add"))
@inject
async def split_add_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    tokens = (command.args or "").split()
    split_id = parse_split_id(tokens, message)
    if split_id is None:
        await message.reply(
            "Usage: <code>/split_add &lt;id&gt; @user [amount]</code> or reply to a split card."
        )
        return

    rest = tokens[1:] if tokens and tokens[0].isdigit() else tokens
    if not rest or not rest[0].startswith("@"):
        await message.reply("Usage: <code>/split_add &lt;id&gt; @user [amount]</code>")
        return

    username_raw = rest[0]
    fixed_amount: str | None = None
    if len(rest) >= 2:
        fixed_amount = _parse_amount(rest[1])
        if fixed_amount is None:
            await message.reply("Invalid amount.")
            return

    actor_entity = await resolve_self(refinance, message.from_user.id, message.from_user.username)
    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return

    target_entity = await _resolve_target_by_username(refinance, user_service, username_raw)
    if target_entity is None:
        await message.reply(f"User {html.escape(username_raw)} not found in refinance.")
        return

    try:
        split = await refinance.upsert_split_participant(
            actor_entity_id=actor_entity["id"],
            split_id=split_id,
            entity_id=target_entity["id"],
            fixed_amount=fixed_amount,
        )
    except Exception as exc:
        await message.reply(f"Failed to add participant: {html.escape(str(exc))}")
        return

    await _refresh_split_card(message, split)


# ------------------------------------------------------------------ #
# /split_leave                                                          #
# ------------------------------------------------------------------ #


@router.message(Command("split_leave"))
@inject
async def split_leave_handler(
    message: Message,
    command: CommandObject,
    refinance: FromDishka[RefinanceClient],
    user_service: FromDishka[UserService],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    tokens = (command.args or "").split()
    split_id = parse_split_id(tokens, message)
    if split_id is None:
        await message.reply(
            "Usage: <code>/split_leave &lt;id&gt; [@user]</code> or reply to a split card."
        )
        return

    rest = tokens[1:] if tokens and tokens[0].isdigit() else tokens
    target_username = rest[0] if rest and rest[0].startswith("@") else None

    actor_entity = await resolve_self(refinance, message.from_user.id, message.from_user.username)
    if actor_entity is None:
        await message.reply(_NOT_LINKED)
        return

    if target_username:
        target_entity = await _resolve_target_by_username(refinance, user_service, target_username)
        if target_entity is None:
            await message.reply(f"User {html.escape(target_username)} not found in refinance.")
            return
        remove_entity_id = target_entity["id"]
        label = html.escape(target_entity["name"])
    else:
        remove_entity_id = actor_entity["id"]
        label = "You"

    try:
        split = await refinance.remove_split_participant(
            actor_entity_id=actor_entity["id"],
            split_id=split_id,
            entity_id=remove_entity_id,
        )
    except Exception as exc:
        await message.reply(f"Failed to leave split: {html.escape(str(exc))}")
        return

    await _refresh_split_card(message, split)


# ------------------------------------------------------------------ #
# /splits                                                               #
# ------------------------------------------------------------------ #


@router.message(Command("splits"))
@inject
async def splits_list_handler(
    message: Message,
    refinance: FromDishka[RefinanceClient],
) -> None:
    if message.from_user is None:
        return
    if not refinance.is_configured:
        await message.reply(_NOT_CONFIGURED)
        return

    entity = await resolve_self(refinance, message.from_user.id, message.from_user.username)
    if entity is None:
        await message.reply(_NOT_LINKED)
        return

    try:
        splits = await refinance.list_splits(entity["id"], performed=False)
    except Exception as exc:
        await message.reply(f"Error: {html.escape(str(exc))}")
        return

    if not splits:
        await message.reply("No open splits.")
        return

    lines = ["<b>Open splits:</b>"]
    for s in splits:
        comment = html.escape(s.get("comment") or "Split")
        recipient = html.escape(s["recipient_entity"]["name"])
        amount = s["amount"]
        currency = (s.get("currency") or "").upper()
        n = len(s.get("participants") or [])
        lines.append(
            f"  • #{s['id']} <b>{comment}</b> → {recipient}  "
            f"{amount} {currency}  ({n} participant{'s' if n != 1 else ''})"
        )

    await message.reply("\n".join(lines))
