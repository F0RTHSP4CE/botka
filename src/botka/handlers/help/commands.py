from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name=__name__)


HELP_TEXT = (
    "Available commands:\n"
    "\n"
    "/start — initialize your user profile.\n"
    "/help — show this help message.\n"
    "\n"
    "Shopping:\n"
    "/need &lt;item&gt; — add item to the shopping list.\n"
    "/needs — show open items with buttons.\n"
    "\n"
    "Administration:\n"
    "/user &lt;telegram_id&gt; &lt;resident|member|guest&gt; — set user tier."
)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)
