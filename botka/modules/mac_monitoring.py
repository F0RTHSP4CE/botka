"""MAC monitoring module for presence detection."""

import asyncio
import logging
from datetime import timedelta
from typing import Set

from telegram import Bot
from telegram.ext import Application
from sqlalchemy import select

from ..db import UserMac, TgUser, get_session
from ..services import get_mikrotik_leases

logger = logging.getLogger(__name__)


async def mac_monitoring_task(app: Application) -> None:
    """Background task for MAC monitoring."""
    from ..bot import state

    while True:
        try:
            if state.config.services.mikrotik:
                await check_mac_presence(app)
        except Exception as e:
            logger.error(f"MAC monitoring error: {e}")

        await asyncio.sleep(60)


async def check_mac_presence(app: Application) -> None:
    """Check MAC presence and notify changes."""
    from ..bot import state

    leases = await get_mikrotik_leases(
        state.http_client,
        state.config.services.mikrotik,
    )

    # Get active MACs (seen in last 20 minutes)
    active_macs = {
        l.mac_address.upper() for l in leases if l.last_seen < timedelta(minutes=20)
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

    # Send notifications
    if (joined or left) and state.config.telegram.chats.mac_monitoring:
        await notify_presence_change(app.bot, joined, left)

    state.active_users = new_active_users


async def notify_presence_change(bot: Bot, joined: Set[int], left: Set[int]) -> None:
    """Send notification about presence changes."""
    from ..bot import state

    if not state.config.telegram.chats.mac_monitoring:
        return

    chat_config = state.config.telegram.chats.mac_monitoring

    # Get user info
    all_users = joined | left
    user_names = {}

    if all_users:
        async with await get_session() as session:
            result = await session.execute(
                select(TgUser).where(TgUser.id.in_(all_users))
            )
            users = result.scalars().all()
            for u in users:
                name = u.first_name
                if u.last_name:
                    name += f" {u.last_name}"
                user_names[u.id] = name

    text = ""

    if left:
        text += "Left space:\n"
        for uid in left:
            name = user_names.get(uid, f"User {uid}")
            text += f"• {name}\n"

    if joined:
        if text:
            text += "\n"
        text += "Joined space:\n"
        for uid in joined:
            name = user_names.get(uid, f"User {uid}")
            text += f"• {name}\n"

    if text:
        await bot.send_message(
            chat_id=chat_config.chat,
            message_thread_id=chat_config.thread,
            text=text,
        )
