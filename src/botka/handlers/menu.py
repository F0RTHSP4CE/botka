from __future__ import annotations

import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.db.models import User, UserTier

router = Router(name=__name__)
router.message.filter(F.chat.type == "private")


class Btn:
    """Centralized button label constants shared by keyboards and handlers."""

    # Main menu
    OPEN_GATE = "🔓 Open Gate"
    OPEN = "🚪 Open"
    BALANCE = "💰 Balance"
    DEPOSIT = "💳 Deposit"
    FRIDGE = "🍱 Fridge"
    TRANSFER = "💸 Transfer"
    OTHER = "➕ Other commands…"
    STATUS = "📡 Status"

    # Guest menu
    ASK_VISIT = "📩 Ask to visit"

    # Other submenu
    NEEDS = "🛒 Shopping list"
    BORROWED = "📦 Borrowed"
    TODO = "✅ Todo"
    BOARDS = "📌 Boards"
    UPS = "🔋 UPS"
    TRANSACTIONS = "💸 Transactions"
    AGENDA = "📅 Agenda"
    NEED_ITEM = "➕ Need item"
    TASK = "📋 Task"
    PERIODIC = "⚙️ Periodic"
    BACK = "← Back"

    # FSM control
    CANCEL = "❌ Cancel"


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=Btn.CANCEL)]],
        resize_keyboard=True,
    )


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.OPEN_GATE), KeyboardButton(text=Btn.OPEN)],
            [KeyboardButton(text=Btn.BALANCE), KeyboardButton(text=Btn.DEPOSIT)],
            [KeyboardButton(text=Btn.FRIDGE), KeyboardButton(text=Btn.TRANSFER)],
            [KeyboardButton(text=Btn.STATUS), KeyboardButton(text=Btn.OTHER)],
        ],
        resize_keyboard=True,
    )


def guest_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.OPEN_GATE), KeyboardButton(text=Btn.ASK_VISIT)],
            [KeyboardButton(text=Btn.NEEDS), KeyboardButton(text=Btn.TODO)],
        ],
        resize_keyboard=True,
    )


def other_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.NEEDS), KeyboardButton(text=Btn.BORROWED)],
            [KeyboardButton(text=Btn.TODO), KeyboardButton(text=Btn.BOARDS)],
            [KeyboardButton(text=Btn.UPS), KeyboardButton(text=Btn.TRANSACTIONS)],
            [KeyboardButton(text=Btn.AGENDA), KeyboardButton(text=Btn.NEED_ITEM)],
            [KeyboardButton(text=Btn.TASK), KeyboardButton(text=Btn.PERIODIC)],
            [KeyboardButton(text=Btn.BACK)],
        ],
        resize_keyboard=True,
    )


def _is_guest(user_record: User | None) -> bool:
    return user_record is None or user_record.tier == UserTier.guest


_TIER_LABEL = {
    UserTier.resident: "resident",
    UserTier.member: "member",
    UserTier.guest: "guest",
}


def _welcome_text(message: Message, user_record: User | None) -> str:
    tg = message.from_user
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    username = (
        f"@{html.escape(tg.username)}" if (tg and tg.username) else "(no username)"
    )
    tier = (
        _TIER_LABEL.get(user_record.tier, user_record.tier.value)
        if user_record
        else "guest"
    )
    return (
        "Welcome, I am <b>F0RTHSP4CE</b> bot.\n\n"
        f"user: {username}\n"
        f"tier: {tier}\n"
        f"date: {now}"
    )


async def send_main_menu(message: Message, user_record: User | None = None) -> None:
    kb = guest_menu_kb() if _is_guest(user_record) else main_menu_kb()
    await message.answer(_welcome_text(message, user_record), reply_markup=kb)


@router.message(Command("menu"))
@inject
async def menu_command(
    message: Message,
    user_record: User | None = None,
) -> None:
    await send_main_menu(message, user_record)


@router.message(F.text == Btn.OTHER)
async def show_other_menu(message: Message) -> None:
    await message.answer("Other:", reply_markup=other_menu_kb())


@router.message(F.text == Btn.ASK_VISIT)
@inject
async def ask_visit_handler(
    message: Message,
    settings: FromDishka[Settings],
) -> None:
    chat_id = settings.visit_request_chat_id
    topic_id = settings.visit_request_topic_id
    if not chat_id:
        await message.reply(
            "Visit-request chat is not configured. Contact an admin directly."
        )
        return
    # Public chat: username (with or without leading @) → t.me/username[/topic]
    # Private supergroup: numeric ID starting with -100 → t.me/c/NNNN[/topic]
    stripped = chat_id.lstrip("@")
    try:
        numeric = int(stripped)
        cid = str(numeric)[4:] if str(numeric).startswith("-100") else str(numeric)
        base = f"https://t.me/c/{cid}"
    except ValueError:
        base = f"https://t.me/{stripped}"
    if topic_id:
        url = f"{base}/{topic_id}"
    else:
        url = base
    await message.answer(
        "Send a message there to ask for a visit:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📩 Request a visit", url=url)]]
        ),
    )


@router.message(F.text == Btn.BACK)
@inject
async def show_main_menu_back(
    message: Message,
    user_record: User | None = None,
) -> None:
    await send_main_menu(message, user_record)


@router.message(F.text == Btn.CANCEL)
@inject
async def cancel_handler(
    message: Message,
    state: FSMContext,
    user_record: User | None = None,
) -> None:
    await state.clear()
    await send_main_menu(message, user_record)
