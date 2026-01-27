"""Main bot application entry point with Dishka dependency injection."""

import asyncio
import logging
import os
import sys
from datetime import timedelta
from typing import Optional

import httpx
from dishka import make_async_container, AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine
from telegram import Update, User
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from . import __version__
from .config import Config, load_config
from .db import Base, TgUser, Resident, UserMac, NeededItem
from .providers import (
    ConfigProvider,
    HttpClientProvider,
    DatabaseProvider,
    SessionProvider,
    StateProvider,
    ServiceProvider,
    ActiveUsers,
)
from .di_services import (
    ResidentService,
    UserService,
    NeedsService,
    MikrotikService,
    CameraService,
    ButlerService,
)
from .dishka_integration import setup_dishka, inject, get_container
from .services import get_mikrotik_leases

# Import modules
from .modules import (
    butler,
    camera,
    userctl,
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


# Global container and state provider (for background tasks and legacy module support)
_container: Optional[AsyncContainer] = None
_state_provider: Optional[StateProvider] = None


# =============================================================================
# Legacy compatibility layer for modules that haven't been migrated to DI
# =============================================================================


class BotState:
    """Legacy global bot state for backward compatibility with modules."""

    def __init__(self):
        self.config: Optional[Config] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.active_users: set[int] = set()


state = BotState()


async def is_resident(user_id: int) -> bool:
    """Check if user is a current resident (legacy helper for modules)."""
    if _container:
        async with _container() as request_container:
            service = await request_container.get(ResidentService)
            return await service.is_resident(user_id)
    return False


async def is_admin(user_id: int) -> bool:
    """Check if user is a bot admin (legacy helper for modules)."""
    if _container:
        config = await _container.get(Config)
        return user_id in config.telegram.admins
    if state.config:
        return user_id in state.config.telegram.admins
    return False


async def get_or_create_user(session: AsyncSession, user: User) -> TgUser:
    """Get or create a TgUser record (legacy helper for modules)."""
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
        db_user.username = user.username
        db_user.first_name = user.first_name
        db_user.last_name = user.last_name
        await session.commit()

    return db_user


# =============================================================================
# Utility functions
# =============================================================================


def format_user(user: User) -> str:
    """Format user for display."""
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    if user.username:
        return f"{name} (@{user.username})"
    return name


# =============================================================================
# Command handlers with dependency injection
# =============================================================================


@inject
async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
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


@inject
async def cmd_version(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show bot version."""
    await update.message.reply_text(f"F0RTHSP4CE Bot (Python) v{__version__}")


@inject
async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    active_users: ActiveUsers,
) -> None:
    """Show bot status."""
    active_count = len(active_users)
    status_text = f"""<b>Bot Status</b>

Active users at space: {active_count}
Version: {__version__}
"""
    await update.message.reply_text(status_text, parse_mode="HTML")


@inject
async def cmd_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mikrotik_service: MikrotikService,
) -> None:
    """Count devices online via Mikrotik."""
    if not mikrotik_service.is_configured:
        await update.message.reply_text("Mikrotik is not configured.")
        return

    await update.message.chat.send_action("typing")

    try:
        leases = await mikrotik_service.get_leases()
        total = len(leases)
        active = sum(1 for lease in leases if lease.last_seen < timedelta(minutes=20))
        await update.message.reply_text(
            f"Devices online: {active} (total leases: {total})"
        )
    except Exception as e:
        logger.error(f"/count failed: {e}")
        await update.message.reply_text(f"Failed to fetch count: {e}")


@inject
async def cmd_residents(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
) -> None:
    """List current residents."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    rows = await resident_service.get_residents()

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


@inject
async def cmd_needs(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    needs_service: NeedsService,
) -> None:
    """Show shopping list."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    rows = await needs_service.get_needs()

    if not rows:
        await update.message.reply_text("No items needed. 🎉")
        return

    text = "<b>Shopping list:</b>\n\n"
    for i, (item, tg_user) in enumerate(rows, 1):
        user_name = tg_user.first_name if tg_user else "Unknown"
        text += f"{i}. {item.item} (by {user_name})\n"

    await update.message.reply_text(text, parse_mode="HTML")


@inject
async def cmd_need(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    needs_service: NeedsService,
    user_service: UserService,
) -> None:
    """Add item to shopping list."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /need <item>")
        return

    item_text = " ".join(context.args)

    await user_service.get_or_create_user(user)
    await needs_service.add_need(
        item=item_text,
        user_id=user.id,
        chat_id=update.message.chat_id,
        message_id=update.message.message_id,
    )

    await update.message.reply_text(f"Added to shopping list: {item_text}")


@inject
async def cmd_open(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    butler_service: ButlerService,
) -> None:
    """Open the door."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not butler_service.is_configured:
        await update.message.reply_text("Door opening is not configured.")
        return

    success = await butler_service.open_door()

    if success:
        logger.info(f"User {format_user(user)} opened the door")
        await update.message.reply_text("🚪 Door opened!")
    else:
        await update.message.reply_text("Failed to open door. Please try again.")


@inject
async def cmd_racovina(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    camera_service: CameraService,
) -> None:
    """Show racovina camera image."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    await update.message.chat.send_action("upload_photo")

    image = await camera_service.get_racovina_image()

    if image:
        await update.message.reply_photo(image)
    else:
        await update.message.reply_text(
            "Camera is not configured or failed to fetch image."
        )


@inject
async def cmd_userctl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    user_service: UserService,
) -> None:
    """Show user settings."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    macs = await user_service.get_user_macs(user.id)

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


@inject
async def cmd_add_mac(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    user_service: UserService,
) -> None:
    """Add MAC address for presence detection."""
    import re

    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /add_mac XX:XX:XX:XX:XX:XX")
        return

    mac = context.args[0].upper()

    if not re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", mac):
        await update.message.reply_text(
            "Invalid MAC address format. Use XX:XX:XX:XX:XX:XX"
        )
        return

    added = await user_service.add_mac(user.id, mac)

    if added:
        await update.message.reply_text(f"MAC address {mac} added.")
    else:
        await update.message.reply_text("This MAC address is already registered.")


@inject
async def cmd_remove_mac(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resident_service: ResidentService,
    user_service: UserService,
) -> None:
    """Remove MAC address."""
    user = update.effective_user
    if not await resident_service.is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_mac XX:XX:XX:XX:XX:XX")
        return

    mac = context.args[0].upper()

    removed = await user_service.remove_mac(user.id, mac)

    if removed:
        await update.message.reply_text(f"MAC address {mac} removed.")
    else:
        await update.message.reply_text("This MAC address is not registered.")


# =============================================================================
# Background tasks
# =============================================================================


async def mac_monitoring_task(
    app: Application,
    config: Config,
    http_client: httpx.AsyncClient,
) -> None:
    """Background task for MAC monitoring."""
    while True:
        try:
            if config.services.mikrotik and _container:
                leases = await get_mikrotik_leases(
                    http_client, config.services.mikrotik
                )

                # Get active MACs (seen in last 20 minutes)
                active_macs = {
                    lease.mac_address.upper()
                    for lease in leases
                    if lease.last_seen < timedelta(minutes=20)
                }

                # Get user IDs for these MACs
                async with _container() as request_container:
                    session = await request_container.get(AsyncSession)
                    result = await session.execute(
                        select(UserMac).where(UserMac.mac.in_(active_macs))
                    )
                    macs = result.scalars().all()

                new_active_users = {mac.tg_id for mac in macs}

                # Get current active users
                current_active = state.active_users

                # Detect changes
                joined = new_active_users - current_active
                left = current_active - new_active_users

                if (joined or left) and config.telegram.chats.mac_monitoring:
                    chat_config = config.telegram.chats.mac_monitoring

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

                # Update state
                state.active_users = new_active_users
                if _state_provider:
                    _state_provider.update_active_users(new_active_users)

        except Exception as e:
            logger.error(f"MAC monitoring error: {e}")

        await asyncio.sleep(60)


async def post_init(app: Application) -> None:
    """Post-initialization hook."""
    global _container

    config = await _container.get(Config)
    http_client = await _container.get(httpx.AsyncClient)

    # Start background tasks
    asyncio.create_task(mac_monitoring_task(app, config, http_client))


async def init_database(container: AsyncContainer) -> None:
    """Initialize database tables."""
    engine = await container.get(AsyncEngine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# =============================================================================
# Main entry point
# =============================================================================


def main() -> None:
    """Main entry point."""
    global _container, _state_provider, state

    # Load configuration
    config_path = os.environ.get("CONFIG_PATH")
    db_path = os.environ.get("DB_PATH", "db.sqlite3")

    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # Set up legacy state for module compatibility
    state.config = config

    logger.info(f"Starting F0RTHSP4CE Bot v{__version__}")

    # Create DI providers
    config_provider = ConfigProvider(config_path)
    http_provider = HttpClientProvider()
    db_provider = DatabaseProvider(db_path)
    session_provider = SessionProvider()
    _state_provider = StateProvider()
    service_provider = ServiceProvider()

    # Create async container
    _container = make_async_container(
        config_provider,
        http_provider,
        db_provider,
        session_provider,
        _state_provider,
        service_provider,
    )

    # Initialize database and HTTP client
    async def init():
        await init_database(_container)
        state.http_client = await _container.get(httpx.AsyncClient)

    asyncio.get_event_loop().run_until_complete(init())

    # Create application
    app = (
        Application.builder().token(config.telegram.token).post_init(post_init).build()
    )

    # Set up Dishka integration
    setup_dishka(_container, app)

    # Register command handlers
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
    for handler in butler.get_handlers():
        app.add_handler(handler)

    for handler in camera.get_handlers():
        app.add_handler(handler)

    for handler in userctl.get_handlers():
        app.add_handler(handler)

    for handler in broadcast.get_handlers():
        app.add_handler(handler)

    for handler in polls.get_handlers():
        app.add_handler(handler)
    app.add_handler(polls.get_poll_answer_handler())

    for handler in borrowed_items.get_handlers():
        app.add_handler(handler)
    app.add_handler(borrowed_items.get_callback_handler())

    app.add_handler(welcome.get_member_handler())
    app.add_handler(ask_to_visit.get_message_handler())

    for handler in vortex_of_doom.get_handlers():
        app.add_handler(handler)

    for handler in tldr.get_handlers():
        app.add_handler(handler)

    for handler in get_nlp_handlers():
        app.add_handler(handler)

    # Start the bot
    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
