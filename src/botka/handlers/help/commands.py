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
    "Doors:\n"
    "/open — open main door and gate (residents/members).\n"
    "/open_gate — open the gate.\n"
    "\n"
    "MAC tracker:\n"
    "/mac — get a device registration link (residents/members).\n"
    "/status — list who is currently in the space.\n"
    "/mac_clear [&lt;telegram_id&gt;] — clear MAC assignments (residents can clear others).\n"
    "\n"
    "Borrowed items:\n"
    "/borrowed — list borrowed items.\n"
    "\n"
    "Shopping:\n"
    "/need &lt;item&gt; — add item to the shopping list.\n"
    "/needs — show open items with buttons.\n"
    "\n"
    "Polls:\n"
    "/poll_close [&lt;poll_id&gt;] — close a poll you created (or reply to the poll/awaiting message).\n"
    "\n"
    "Periodic:\n"
    "/periodic — list periodic jobs.\n"
    "/periodic_run &lt;job_name&gt; — run a periodic job now (residents only).\n"
    "  Example: /periodic_run poll_maintenance — refresh poll timers and auto-close due polls.\n"
    "\n"
    "Administration:\n"
    "/user [&lt;resident|member|guest&gt; [&lt;telegram_id&gt;]] — view or set user tier (or reply)."
)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)
