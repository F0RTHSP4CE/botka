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
    "Tasks (residents/members):\n"
    "/todo [&lt;task_name&gt;] — create a task in TODO or list TODO/IN PROGRESS tasks.\n"
    "/task &lt;id&gt; — show full task details (title, description, checklist, attachments).\n"
    "/attach &lt;id&gt; — attach a file to task (send file with command or reply to file).\n"
    "/doing &lt;id&gt; — move task to IN PROGRESS.\n"
    "/done [&lt;id&gt;] — move task to DONE, or show recent DONE tasks with no args.\n"
    "/boards — list all task boards.\n"
    "\n"
    "Periodic:\n"
    "/periodic — list periodic jobs.\n"
    "/periodic_run &lt;job_name&gt; — run a periodic job now (residents only).\n"
    "  Example: /periodic_run poll_maintenance — refresh poll timers and auto-close due polls.\n"
    "\n"
    "Administration:\n"
    "/user [&lt;resident|member|guest&gt; [&lt;telegram_id&gt;]] — view or set user tier (or reply).\n"
    "\n"
    "Finance (refinance):\n"
    "/transfer [@username | reply] &lt;amount&gt; &lt;currency&gt; [comment] — send money (creates a draft, you confirm).\n"
    "/request [@username | reply] &lt;amount&gt; &lt;currency&gt; [comment] — request money from someone (they confirm or deny).\n"
    "/balance [@username] — show balance and recent activity (yours or another person's).\n"
    "/deposit &lt;amount&gt; &lt;currency&gt; — top up your balance with a card.\n"
    "/transactions — show your last 10 transactions."
)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)
