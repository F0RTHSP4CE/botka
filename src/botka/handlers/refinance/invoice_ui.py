from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

AUTO_PAY_GRACE_DAYS = 7
ROOM_TAG_ID = 19


@dataclass(frozen=True)
class PaymentLine:
    item_id: int | None
    recipient_id: int
    recipient_name: str
    amounts: dict[str, Decimal]


@dataclass(frozen=True)
class PaymentView:
    invoice_id: int
    lines: list[PaymentLine]
    currencies: list[str]
    totals: dict[str, Decimal]
    selectable_item_id: int | None = None
    selectable_tag_id: int | None = None
    selected_room_id: int | None = None
    selected_room_name: str | None = None


def _amount_map(entries: list[dict]) -> dict[str, Decimal]:
    return {
        str(entry["currency"]).lower(): Decimal(str(entry["amount"]))
        for entry in entries
    }


def _amount_text(amount: Decimal) -> str:
    value = format(amount, "f")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value or "0"


def _recipient(entity: dict | None, fallback_id: int | None) -> tuple[int, str]:
    if entity is not None:
        return int(entity["id"]), str(entity["name"])
    if fallback_id is None:
        raise ValueError("Invoice item has no recipient")
    return fallback_id, f"entity {fallback_id}"


def selectable_room_recipient(invoice: dict) -> tuple[int | None, int | None]:
    """Return the room tag and current room recipient for a monthly fee."""
    for item in invoice.get("items") or []:
        raw_tag_id = item.get("to_tag_id")
        if raw_tag_id is not None and int(raw_tag_id) == ROOM_TAG_ID:
            entity_id = item.get("to_entity_id")
            return ROOM_TAG_ID, int(entity_id) if entity_id is not None else None
    return None, None


def build_payment_view(invoice: dict, room_entity: dict | None = None) -> PaymentView:
    lines: list[PaymentLine] = []
    selectable_item_id = None
    selectable_tag_id = None
    selected_room_id = None
    selected_room_name = None

    items = invoice.get("items") or []
    if items:
        for item in items:
            entity = item.get("to_entity")
            entity_id = item.get("to_entity_id")
            if (
                item.get("to_tag_id") is not None
                and int(item["to_tag_id"]) == ROOM_TAG_ID
                and selectable_item_id is None
            ):
                selectable_item_id = int(item["id"])
                selectable_tag_id = int(item["to_tag_id"])
                if room_entity is not None:
                    entity = room_entity
                    entity_id = room_entity["id"]
                if entity_id is not None:
                    selected_room_id = int(entity_id)
                    selected_room_name = str(
                        (entity or {}).get("name") or f"entity {entity_id}"
                    )
            recipient_id, recipient_name = _recipient(entity, entity_id)
            lines.append(
                PaymentLine(
                    item_id=int(item["id"]),
                    recipient_id=recipient_id,
                    recipient_name=recipient_name,
                    amounts=_amount_map(item.get("amounts") or []),
                )
            )
    else:
        recipient_id, recipient_name = _recipient(
            invoice.get("to_entity"), invoice.get("to_entity_id")
        )
        lines.append(
            PaymentLine(
                item_id=None,
                recipient_id=recipient_id,
                recipient_name=recipient_name,
                amounts=_amount_map(invoice.get("amounts") or []),
            )
        )

    currencies: list[str] = []
    if lines:
        for currency in lines[0].amounts:
            if all(currency in line.amounts for line in lines):
                currencies.append(currency)
    totals = {
        currency: sum((line.amounts[currency] for line in lines), start=Decimal("0"))
        for currency in currencies
    }
    return PaymentView(
        invoice_id=int(invoice["id"]),
        lines=lines,
        currencies=currencies,
        totals=totals,
        selectable_item_id=selectable_item_id,
        selectable_tag_id=selectable_tag_id,
        selected_room_id=selected_room_id,
        selected_room_name=selected_room_name,
    )


def affordable_currencies(view: PaymentView, balance: dict) -> list[str]:
    completed = balance.get("completed") or {}
    return [
        currency
        for currency in view.currencies
        if Decimal(str(completed.get(currency, "0"))) >= view.totals[currency]
    ]


def payment_items(view: PaymentView, currency: str) -> list[dict]:
    currency = currency.lower()
    if currency not in view.currencies:
        raise ValueError("Currency is not available for every invoice item")
    return [
        {
            "item_id": line.item_id,
            "to_entity_id": line.recipient_id,
            "currency": currency,
            "amount": str(line.amounts[currency]),
        }
        for line in view.lines
        if line.item_id is not None
    ]


def _period_label(invoice: dict) -> str:
    raw = invoice.get("billing_period")
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return str(raw)
    return parsed.strftime("%B %Y")


def _auto_pay_label(invoice: dict) -> str:
    raw = invoice.get("created_at")
    if not raw:
        return "after the seven-day grace period"
    try:
        created_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "after the seven-day grace period"
    eligible_at = created_at + timedelta(days=AUTO_PAY_GRACE_DAYS)
    return f"{eligible_at.strftime('%B')} {eligible_at.day}"


def _line_alternatives(line: PaymentLine) -> str:
    amounts = " / ".join(
        f"{_amount_text(amount)} {currency.upper()}"
        for currency, amount in line.amounts.items()
    )
    return f"{amounts} → {html.escape(line.recipient_name)}"


def _total_alternatives(view: PaymentView) -> str:
    return " or ".join(
        f"{_amount_text(view.totals[currency])} {currency.upper()}"
        for currency in view.currencies
    )


def format_notification(invoice: dict, *, is_new: bool) -> str:
    view = build_payment_view(invoice)
    prefix = "" if is_new else "Pending "
    period = _period_label(invoice)
    title = f"🧾 <b>{prefix}monthly fee invoice #{view.invoice_id}</b>"
    if period:
        title += f" — {html.escape(period)}"
    lines = [title, ""]
    lines.extend(_line_alternatives(line) for line in view.lines)
    if view.totals:
        lines.extend(["", f"<b>Total:</b> {_total_alternatives(view)}"])
    lines.append(f"<b>Automatic payment:</b> {_auto_pay_label(invoice)}")
    if view.selected_room_name:
        lines.append(
            f"<b>Default donation room:</b> {html.escape(view.selected_room_name)}"
        )
    return "\n".join(lines)


def notification_keyboard(invoice: dict, telegram_id: int) -> InlineKeyboardMarkup:
    view = build_payment_view(invoice)
    rows = [
        [
            InlineKeyboardButton(
                text="Review and pay",
                callback_data=f"rfi:v:n:{view.invoice_id}:0:{telegram_id}",
            )
        ]
    ]
    if view.selectable_tag_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Choose another room",
                    callback_data=f"rfi:r:n:{view.invoice_id}:0:{telegram_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_review(view: PaymentView, balance: dict) -> str:
    lines = [f"🧾 <b>Pay invoice #{view.invoice_id}?</b>"]
    lines.extend(f"    {_line_alternatives(line)}" for line in view.lines)
    if view.totals:
        lines.append(f"    <b>Total:</b> {_total_alternatives(view)}")
    affordable = affordable_currencies(view, balance)
    if affordable:
        lines.extend(["", "Choose the currency to confirm payment."])
    else:
        completed = balance.get("completed") or {}
        balance_lines = [
            f"    {html.escape(str(value))} {html.escape(currency.upper())}"
            for currency, value in completed.items()
        ] or ["    0"]
        lines.extend(
            [
                "",
                "⚠️ <b>Your balance is too low</b>",
                *balance_lines,
                "",
                "Use /deposit to top up before paying this invoice.",
            ]
        )
    return "\n".join(lines)


def review_keyboard(
    view: PaymentView,
    balance: dict,
    *,
    origin: str,
    telegram_id: int,
    recommended_deposit: dict | None = None,
) -> InlineKeyboardMarkup:
    room_id = view.selected_room_id or 0
    rows: list[list[InlineKeyboardButton]] = []
    affordable = affordable_currencies(view, balance)
    if affordable:
        for currency in affordable:
            total = _amount_text(view.totals[currency])
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Pay {total} {currency.upper()}",
                        callback_data=(
                            f"rfi:p:{origin}:{view.invoice_id}:{room_id}:"
                            f"{currency}:{telegram_id}"
                        ),
                    )
                ]
            )
        if view.selectable_tag_id is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Choose another room",
                        callback_data=(
                            f"rfi:r:{origin}:{view.invoice_id}:{room_id}:{telegram_id}"
                        ),
                    )
                ]
            )
    else:
        deposit_label = "Deposit"
        if recommended_deposit:
            raw_amount = recommended_deposit.get("amount")
            currency = str(recommended_deposit.get("currency") or "").upper()
            try:
                amount = Decimal(str(raw_amount))
            except Exception:
                amount = Decimal("0")
            if amount > 0 and currency:
                deposit_label = f"Deposit {_amount_text(amount)} {currency}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=deposit_label,
                    callback_data=f"rfi:d:{view.invoice_id}:{telegram_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Check balance again",
                    callback_data=(
                        f"rfi:v:{origin}:{view.invoice_id}:{room_id}:{telegram_id}"
                    ),
                )
            ]
        )
    back_data = (
        f"rfi:bal:{telegram_id}"
        if origin == "b"
        else f"rfi:n:{view.invoice_id}:{telegram_id}"
    )
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def room_keyboard(
    invoice_id: int,
    rooms: list[dict],
    current_room_id: int | None,
    *,
    origin: str,
    telegram_id: int,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✓ " if room["id"] == current_room_id else "") + room["name"],
            callback_data=(f"rfi:s:{origin}:{invoice_id}:{room['id']}:{telegram_id}"),
        )
        for room in rooms
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    if current_room_id:
        back_data = f"rfi:v:{origin}:{invoice_id}:{current_room_id}:{telegram_id}"
    elif origin == "b":
        back_data = f"rfi:bal:{telegram_id}"
    else:
        back_data = f"rfi:n:{invoice_id}:{telegram_id}"
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_receipt(view: PaymentView, currency: str) -> str:
    currency = currency.lower()
    lines = [f"✅ <b>Invoice #{view.invoice_id} paid</b>", ""]
    lines.extend(
        f"{_amount_text(line.amounts[currency])} {currency.upper()} → "
        f"{html.escape(line.recipient_name)}"
        for line in view.lines
    )
    lines.extend(
        [
            "",
            f"<b>Total:</b> {_amount_text(view.totals[currency])} "
            f"{currency.upper()}",
        ]
    )
    return "\n".join(lines)
