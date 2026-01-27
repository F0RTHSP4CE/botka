"""Basic bot commands: help, status, version, residents, count, topics."""

import logging
from datetime import timedelta

from telegram import Update, User
from telegram.ext import ContextTypes

from ..db import TgUser, Resident, get_session
from ..services import get_mikrotik_leases
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)


def format_user(user: User) -> str:
    """Format user for display."""
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    if user.username:
        return f"{name} (@{user.username})"
    return name


def format_user_html(user: User) -> str:
    """Format user as HTML link."""
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display available commands."""
    from ..bot import __version__

    help_text = """<b>Available commands:</b>

<b>Basic:</b>
/help - display this text
/status - show bot status
/version - show bot version
/count - count devices online (Mikrotik)

<b>Resident commands (*)</b>
/residents - list current residents
/needs - show shopping list
/need &lt;item&gt; - add item to shopping list
/open - open the door
/temp_open - generate temporary guest access link
/racovina - show racovina camera
/hlam - show hlam camera

<b>Polls:</b>
Start poll question with ! to make it tracked

<b>User settings (*)</b>
/userctl - show your settings
/add_mac XX:XX:XX:XX:XX:XX - add MAC for presence detection
/remove_mac XX:XX:XX:XX:XX:XX - remove MAC address
/add_ssh &lt;public_key&gt; - add SSH key
/get_ssh &lt;username&gt; - get user's SSH keys

<b>Admin commands (**):</b>
/broadcast - broadcast message to all residents
/add_resident &lt;username/id&gt; - add resident
/remove_resident &lt;username/id&gt; - remove resident

<b>AI/NLP:</b>
/tldr - summarize discussion (TL;DR)
Mention bot name to ask questions

Commands marked with * are available only to residents.
Commands marked with ** are available only to admins.
"""
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot version."""
    from .. import __version__

    await update.message.reply_text(f"F0RTHSP4CE Bot (Python) v{__version__}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot status."""
    from ..bot import state
    from .. import __version__

    active_count = len(state.active_users)
    status_text = f"""<b>Bot Status</b>

Active users at space: {active_count}
Version: {__version__}
"""
    await update.message.reply_text(status_text, parse_mode="HTML")


async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Count devices online via Mikrotik."""
    from ..bot import state

    if not state.config.services.mikrotik:
        await update.message.reply_text("Mikrotik is not configured.")
        return

    await update.message.chat.send_action("typing")

    try:
        leases = await get_mikrotik_leases(
            state.http_client,
            state.config.services.mikrotik,
        )
        total = len(leases)
        active = sum(1 for l in leases if l.last_seen < timedelta(minutes=20))
        await update.message.reply_text(
            f"Devices online: {active} (total leases: {total})"
        )
    except Exception as e:
        logger.error(f"/count failed: {e}")
        await update.message.reply_text(f"Failed to fetch count: {e}")


async def cmd_residents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List current residents."""
    from ..bot import is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    async with await get_session() as session:
        result = await session.execute(
            select(Resident, TgUser)
            .outerjoin(TgUser, Resident.tg_id == TgUser.id)
            .where(Resident.end_date.is_(None))
            .order_by(Resident.begin_date.desc())
        )
        rows = result.all()

    if not rows:
        await update.message.reply_text("No residents found.")
        return

    text = "<b>Current residents:</b>\n\n"
    for resident, tg_user in rows:
        if tg_user:
            name = tg_user.first_name
            if tg_user.last_name:
                name += f" {tg_user.last_name}"
            if tg_user.username:
                text += f"• {name} (@{tg_user.username})\n"
            else:
                text += f"• {name}\n"
        else:
            text += f"• User ID: {resident.tg_id}\n"

    await update.message.reply_text(text, parse_mode="HTML")
