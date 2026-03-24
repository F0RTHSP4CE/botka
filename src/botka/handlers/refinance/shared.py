"""Shared helpers for refinance handlers."""

from __future__ import annotations

import html
import re
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from botka.services.refinance_client import RefinanceClient


async def resolve_self(
    client: RefinanceClient,
    telegram_id: int,
    username: str | None,
) -> dict | None:
    try:
        return await client.get_or_link_entity(telegram_id, username)
    except Exception:
        return None


def parse_split_id(args: list[str], message: Message) -> int | None:
    """Return split ID from explicit arg or by parsing a replied-to split card."""
    if args and args[0].isdigit():
        return int(args[0])
    if message.reply_to_message:
        text = (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or ""
        )
        m = re.search(r"\(#(\d+)\)", text)
        if m:
            return int(m.group(1))
    return None


def format_split_card(split: dict) -> str:
    split_id = split["id"]
    comment = split.get("comment") or "Split"
    recipient_name = html.escape(split["recipient_entity"]["name"])
    currency = (split.get("currency") or "").upper()
    total = Decimal(str(split["amount"]))
    collected = Decimal(str(split.get("collected_amount") or 0))
    remaining = total - collected
    participants = split.get("participants") or []
    share_preview = split.get("share_preview") or {}
    current_share = share_preview.get("current_share")
    next_share = share_preview.get("next_share")
    performed = split.get("performed", False)

    lines = [f"💸 <b>{html.escape(comment)}</b>  (#{split_id})"]
    lines.append(
        f"Recipient: <b>{recipient_name}</b>  •  {total} {currency}"
    )

    if participants:
        lines.append(f"\nParticipants ({len(participants)}):")
        for p in participants:
            name = html.escape(p["entity"]["name"])
            fa = p.get("fixed_amount")
            if fa is not None:
                lines.append(f"  • {name} — {fa} {currency} (fixed)")
            else:
                share_str = str(current_share) if current_share is not None else "auto"
                lines.append(f"  • {name} — {share_str} {currency} (auto)")
    else:
        lines.append("\nParticipants: none yet")

    lines.append(
        f"\nCollected: {collected} {currency}  •  Remaining: {remaining} {currency}"
    )

    if performed:
        txs = split.get("performed_transactions") or []
        lines.append(f"\n✅ Performed — {len(txs)} transaction(s) created.")

    return "\n".join(lines)


def split_keyboard(split: dict) -> InlineKeyboardMarkup:
    split_id = split["id"]
    currency = (split.get("currency") or "").upper()
    share_preview = split.get("share_preview") or {}
    next_share = share_preview.get("next_share", "?")
    performed = split.get("performed", False)

    actor_auth = (split["actor_entity"].get("auth") or {})
    actor_tid = actor_auth.get("telegram_id") or 0

    if performed:
        return InlineKeyboardMarkup(inline_keyboard=[])

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"➕ Join {next_share} {currency}",
                    callback_data=f"rf_split:join:{split_id}",
                ),
                InlineKeyboardButton(
                    text="🚪 Leave",
                    callback_data=f"rf_split:leave:{split_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Perform",
                    callback_data=f"rf_split:perform:{split_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"rf_split:cancel:{split_id}:{actor_tid}",
                ),
            ],
        ]
    )
