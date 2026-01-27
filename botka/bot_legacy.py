"""Main bot application entry point."""

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

import httpx
from telegram import Update, User
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ChatMemberHandler,
    MessageHandler,
    filters,
)
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .config import Config, load_config
from .db import (
    TgUser,
    Resident,
    UserMac,
    NeededItem,
    init_db,
    get_session,
)
from .services import get_mikrotik_leases, get_camera_image, open_door

# Import modules
from .modules import (
    basic,
    needs,
    butler,
    camera,
    userctl,
    mac_monitoring,
    resident_tracker,
    broadcast,
    polls,
    borrowed_items,
    welcome,
    ask_to_visit,
    vortex_of_doom,
    tldr,
)
from .modules.nlp import get_message_handlers as get_nlp_handlers

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# Global state
class BotState:
    """Global bot state."""

    def __init__(self):
        self.config: Optional[Config] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.active_users: set[int] = set()  # Set of user IDs currently at space


state = BotState()


# Helper functions
async def is_resident(user_id: int) -> bool:
    """Check if user is a current resident."""
    async with await get_session() as session:
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == user_id, Resident.end_date.is_(None))
            )
        )
        return result.scalar_one_or_none() is not None


async def is_admin(user_id: int) -> bool:
    """Check if user is a bot admin."""
    return user_id in state.config.telegram.admins


async def get_or_create_user(session: AsyncSession, user: User) -> TgUser:
    """Get or create a TgUser record."""
    result = await session.execute(select(TgUser).where(TgUser.id == user.id))
    db_user = result.scalar_one_or_none()

    if db_user is None:
        db_user = TgUser(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        session.add(db_user)
        await session.commit()
    else:
        # Update user info
        db_user.username = user.username
        db_user.first_name = user.first_name
        db_user.last_name = user.last_name
        await session.commit()

    return db_user


def format_user(user: User) -> str:
    """Format user for display."""
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    if user.username:
        return f"{name} (@{user.username})"
    return name


# Command handlers
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display available commands."""
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
/racovina - show racovina camera

<b>User settings (*)</b>
/userctl - show your settings
/add_mac XX:XX:XX:XX:XX:XX - add MAC for presence detection
/remove_mac XX:XX:XX:XX:XX:XX - remove MAC address

Commands marked with * are available only to residents.
"""
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot version."""
    await update.message.reply_text(f"F0RTHSP4CE Bot (Python) v{__version__}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot status."""
    active_count = len(state.active_users)
    status_text = f"""<b>Bot Status</b>

Active users at space: {active_count}
Version: {__version__}
"""
    await update.message.reply_text(status_text, parse_mode="HTML")


async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Count devices online via Mikrotik."""
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


async def cmd_needs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show shopping list."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    async with await get_session() as session:
        result = await session.execute(
            select(NeededItem, TgUser)
            .outerjoin(TgUser, NeededItem.request_user_id == TgUser.id)
            .where(NeededItem.buyer_user_id.is_(None))
            .order_by(NeededItem.rowid)
        )
        rows = result.all()

    if not rows:
        await update.message.reply_text("No items needed. 🎉")
        return

    text = "<b>Shopping list:</b>\n\n"
    for i, (item, tg_user) in enumerate(rows, 1):
        user_name = tg_user.first_name if tg_user else "Unknown"
        text += f"{i}. {item.item} (by {user_name})\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_need(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add item to shopping list."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /need <item>")
        return

    item_text = " ".join(context.args)

    async with await get_session() as session:
        await get_or_create_user(session, user)

        new_item = NeededItem(
            request_chat_id=update.message.chat_id,
            request_message_id=update.message.message_id,
            request_user_id=user.id,
            pinned_chat_id=update.message.chat_id,
            pinned_message_id=update.message.message_id,
            item=item_text,
        )
        session.add(new_item)
        await session.commit()

    await update.message.reply_text(f"Added to shopping list: {item_text}")


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the door."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not state.config.services.butler:
        await update.message.reply_text("Door opening is not configured.")
        return

    butler = state.config.services.butler
    success = await open_door(state.http_client, butler.url, butler.token)

    if success:
        logger.info(f"User {format_user(user)} opened the door")
        await update.message.reply_text("🚪 Door opened!")
    else:
        await update.message.reply_text("Failed to open door. Please try again.")


async def cmd_racovina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show racovina camera image."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not state.config.services.racovina_cam:
        await update.message.reply_text("Camera is not configured.")
        return

    await update.message.chat.send_action("upload_photo")

    image = await get_camera_image(
        state.http_client,
        state.config.services.racovina_cam,
    )

    if image:
        await update.message.reply_photo(image)
    else:
        await update.message.reply_text("Failed to fetch camera image.")


async def cmd_userctl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user settings."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    async with await get_session() as session:
        result = await session.execute(select(UserMac).where(UserMac.tg_id == user.id))
        macs = result.scalars().all()

    text = f"<b>Your settings ({format_user(user)}):</b>\n\n"
    text += "<b>MAC addresses:</b>\n"
    if macs:
        for mac in macs:
            text += f"• {mac.mac}\n"
    else:
        text += "No MAC addresses registered.\n"

    text += "\n<b>Commands:</b>\n"
    text += "/add_mac XX:XX:XX:XX:XX:XX - add MAC\n"
    text += "/remove_mac XX:XX:XX:XX:XX:XX - remove MAC\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_add_mac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add MAC address for presence detection."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /add_mac XX:XX:XX:XX:XX:XX")
        return

    mac = context.args[0].upper()

    # Basic MAC validation
    import re

    if not re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", mac):
        await update.message.reply_text(
            "Invalid MAC address format. Use XX:XX:XX:XX:XX:XX"
        )
        return

    async with await get_session() as session:
        # Check if already exists
        result = await session.execute(
            select(UserMac).where(and_(UserMac.tg_id == user.id, UserMac.mac == mac))
        )
        if result.scalar_one_or_none():
            await update.message.reply_text("This MAC address is already registered.")
            return

        new_mac = UserMac(tg_id=user.id, mac=mac)
        session.add(new_mac)
        await session.commit()

    await update.message.reply_text(f"MAC address {mac} added.")


async def cmd_remove_mac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove MAC address."""
    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_mac XX:XX:XX:XX:XX:XX")
        return

    mac = context.args[0].upper()

    async with await get_session() as session:
        result = await session.execute(
            select(UserMac).where(and_(UserMac.tg_id == user.id, UserMac.mac == mac))
        )
        mac_record = result.scalar_one_or_none()

        if not mac_record:
            await update.message.reply_text("This MAC address is not registered.")
            return

        await session.delete(mac_record)
        await session.commit()

    await update.message.reply_text(f"MAC address {mac} removed.")


# Background tasks
async def mac_monitoring_task(app: Application) -> None:
    """Background task for MAC monitoring."""
    while True:
        try:
            if state.config.services.mikrotik:
                leases = await get_mikrotik_leases(
                    state.http_client,
                    state.config.services.mikrotik,
                )

                # Get active MACs (seen in last 20 minutes)
                active_macs = {
                    l.mac_address.upper()
                    for l in leases
                    if l.last_seen < timedelta(minutes=20)
                }

                # Get user IDs for these MACs
                async with await get_session() as session:
                    result = await session.execute(
                        select(UserMac).where(UserMac.mac.in_(active_macs))
                    )
                    macs = result.scalars().all()

                new_active_users = {mac.tg_id for mac in macs}

                # Detect changes
                joined = new_active_users - state.active_users
                left = state.active_users - new_active_users

                if (joined or left) and state.config.telegram.chats.mac_monitoring:
                    chat_config = state.config.telegram.chats.mac_monitoring

                    text = ""
                    if left:
                        text += "Left space:\n"
                        for uid in left:
                            text += f"• User {uid}\n"
                    if joined:
                        if text:
                            text += "\n"
                        text += "Joined space:\n"
                        for uid in joined:
                            text += f"• User {uid}\n"

                    if text:
                        await app.bot.send_message(
                            chat_id=chat_config.chat,
                            message_thread_id=chat_config.thread,
                            text=text,
                        )

                state.active_users = new_active_users
        except Exception as e:
            logger.error(f"MAC monitoring error: {e}")

        await asyncio.sleep(60)


async def post_init(app: Application) -> None:
    """Post-initialization hook."""
    # Start background tasks
    asyncio.create_task(mac_monitoring_task(app))


def main() -> None:
    """Main entry point."""
    # Load configuration
    try:
        state.config = load_config()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logger.info(f"Starting F0RTHSP4CE Bot v{__version__}")

    # Initialize HTTP client
    state.http_client = httpx.AsyncClient(verify=False)

    # Initialize database
    asyncio.get_event_loop().run_until_complete(init_db())

    # Create application
    app = (
        Application.builder()
        .token(state.config.telegram.token)
        .post_init(post_init)
        .build()
    )

    # Register command handlers - basic
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("residents", cmd_residents))
    app.add_handler(CommandHandler("needs", cmd_needs))
    app.add_handler(CommandHandler("need", cmd_need))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("racovina", cmd_racovina))
    app.add_handler(CommandHandler("userctl", cmd_userctl))
    app.add_handler(CommandHandler("add_mac", cmd_add_mac))
    app.add_handler(CommandHandler("remove_mac", cmd_remove_mac))

    # Register module handlers
    # Butler module
    for handler in butler.get_handlers():
        app.add_handler(handler)

    # Camera module
    for handler in camera.get_handlers():
        app.add_handler(handler)

    # Userctl module
    for handler in userctl.get_handlers():
        app.add_handler(handler)

    # Broadcast module
    for handler in broadcast.get_handlers():
        app.add_handler(handler)

    # Polls module
    for handler in polls.get_handlers():
        app.add_handler(handler)
    app.add_handler(polls.get_poll_answer_handler())

    # Borrowed items module
    for handler in borrowed_items.get_handlers():
        app.add_handler(handler)
    app.add_handler(borrowed_items.get_callback_handler())

    # Welcome module
    app.add_handler(welcome.get_member_handler())

    # Ask to visit module
    app.add_handler(ask_to_visit.get_message_handler())

    # Vortex of doom module
    for handler in vortex_of_doom.get_handlers():
        app.add_handler(handler)

    # TLDR module
    for handler in tldr.get_handlers():
        app.add_handler(handler)

    # NLP handlers (should be last to catch remaining messages)
    for handler in get_nlp_handlers():
        app.add_handler(handler)

    # Start the bot
    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
