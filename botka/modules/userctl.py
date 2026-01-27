"""User control module for MAC addresses, SSH keys, and resident management."""

import logging
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, and_, delete

from ..db import UserMac, UserSshKey, TgUser, Resident, get_session

logger = logging.getLogger(__name__)

MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


async def cmd_userctl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user settings."""
    from ..bot import is_resident
    from .basic import format_user

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    async with await get_session() as session:
        # Get MACs
        result = await session.execute(select(UserMac).where(UserMac.tg_id == user.id))
        macs = result.scalars().all()

        # Get SSH keys
        result = await session.execute(
            select(UserSshKey).where(UserSshKey.tg_id == user.id)
        )
        ssh_keys = result.scalars().all()

    text = f"<b>Your settings ({format_user(user)}):</b>\n\n"

    text += "<b>MAC addresses:</b>\n"
    if macs:
        for mac in macs:
            text += f"• <code>{mac.mac}</code>\n"
    else:
        text += "No MAC addresses registered.\n"

    text += "\n<b>SSH keys:</b>\n"
    if ssh_keys:
        for key in ssh_keys:
            # Show only the type and comment
            parts = key.key.split()
            if len(parts) >= 2:
                key_type = parts[0]
                comment = parts[2] if len(parts) > 2 else ""
                text += f"• {key_type} {comment}\n"
            else:
                text += f"• {key.key[:40]}...\n"
    else:
        text += "No SSH keys registered.\n"

    text += "\n<b>Commands:</b>\n"
    text += "<code>/add_mac XX:XX:XX:XX:XX:XX</code> - add MAC\n"
    text += "<code>/remove_mac XX:XX:XX:XX:XX:XX</code> - remove MAC\n"
    text += "<code>/add_ssh &lt;key&gt;</code> - add SSH key\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_add_mac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add MAC address for presence detection."""
    from ..bot import is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /add_mac XX:XX:XX:XX:XX:XX")
        return

    mac = context.args[0].upper()

    if not MAC_PATTERN.match(mac):
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

    await update.message.reply_text(f"✅ MAC address {mac} added.")


async def cmd_remove_mac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove MAC address."""
    from ..bot import is_resident

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

    await update.message.reply_text(f"✅ MAC address {mac} removed.")


def is_valid_ssh_key(key: str) -> bool:
    """Validate SSH public key format."""
    key = key.strip()
    valid_types = [
        "ssh-rsa",
        "ssh-ed25519",
        "ssh-dss",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    ]

    parts = key.split()
    if len(parts) < 2:
        return False

    return parts[0] in valid_types


async def cmd_add_ssh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add SSH public key."""
    from ..bot import is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /add_ssh <public_key>")
        return

    ssh_key = " ".join(context.args).strip()

    if not is_valid_ssh_key(ssh_key):
        await update.message.reply_text(
            "Invalid SSH key format. Please provide a valid public SSH key."
        )
        return

    async with await get_session() as session:
        # Check key count
        result = await session.execute(
            select(UserSshKey).where(UserSshKey.tg_id == user.id)
        )
        keys = result.scalars().all()

        if len(keys) >= 10:
            await update.message.reply_text(
                "You have reached the maximum limit of 10 SSH keys. "
                "Please contact an admin to remove old keys."
            )
            return

        # Check if key already exists
        for existing_key in keys:
            if existing_key.key == ssh_key:
                await update.message.reply_text("This SSH key is already registered.")
                return

        new_key = UserSshKey(tg_id=user.id, key=ssh_key)
        session.add(new_key)
        await session.commit()

    await update.message.reply_text("✅ SSH key added successfully.")


async def cmd_get_ssh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get SSH keys of a user by username."""
    from ..bot import is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /get_ssh <username>")
        return

    username = context.args[0].lstrip("@")

    async with await get_session() as session:
        # Find user by username
        result = await session.execute(
            select(TgUser).where(TgUser.username == username)
        )
        tg_user = result.scalar_one_or_none()

        if not tg_user:
            await update.message.reply_text(f"User @{username} not found.")
            return

        # Check if target is resident
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == tg_user.id, Resident.end_date.is_(None))
            )
        )
        if not result.scalar_one_or_none():
            await update.message.reply_text(f"User @{username} is not a resident.")
            return

        # Get SSH keys
        result = await session.execute(
            select(UserSshKey).where(UserSshKey.tg_id == tg_user.id)
        )
        keys = result.scalars().all()

    if not keys:
        await update.message.reply_text(f"User @{username} has no SSH keys registered.")
        return

    text = f"<b>SSH keys for @{username}:</b>\n\n"
    for key in keys:
        text += f"<code>{key.key}</code>\n\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_add_resident(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a user as resident (admin only)."""
    from ..bot import is_admin

    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("This command is only available to admins.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /add_resident <username or user_id>")
        return

    target = context.args[0].lstrip("@")

    async with await get_session() as session:
        # Try to find by username first
        result = await session.execute(select(TgUser).where(TgUser.username == target))
        tg_user = result.scalar_one_or_none()

        # Try by ID if not found
        if not tg_user:
            try:
                user_id = int(target)
                result = await session.execute(
                    select(TgUser).where(TgUser.id == user_id)
                )
                tg_user = result.scalar_one_or_none()
            except ValueError:
                pass

        if not tg_user:
            await update.message.reply_text(f"User '{target}' not found in database.")
            return

        # Check if already resident
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == tg_user.id, Resident.end_date.is_(None))
            )
        )
        if result.scalar_one_or_none():
            await update.message.reply_text(f"User is already a resident.")
            return

        # Add as resident
        new_resident = Resident(tg_id=tg_user.id, begin_date=datetime.utcnow())
        session.add(new_resident)
        await session.commit()

    name = tg_user.first_name
    if tg_user.username:
        name = f"@{tg_user.username}"

    await update.message.reply_text(f"✅ {name} is now a resident.")


async def cmd_remove_resident(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Remove a user from residents (admin only)."""
    from ..bot import is_admin

    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("This command is only available to admins.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_resident <username or user_id>")
        return

    target = context.args[0].lstrip("@")

    async with await get_session() as session:
        # Try to find by username first
        result = await session.execute(select(TgUser).where(TgUser.username == target))
        tg_user = result.scalar_one_or_none()

        # Try by ID if not found
        if not tg_user:
            try:
                user_id = int(target)
                result = await session.execute(
                    select(TgUser).where(TgUser.id == user_id)
                )
                tg_user = result.scalar_one_or_none()
            except ValueError:
                pass

        if not tg_user:
            await update.message.reply_text(f"User '{target}' not found in database.")
            return

        # Find active residency
        result = await session.execute(
            select(Resident).where(
                and_(Resident.tg_id == tg_user.id, Resident.end_date.is_(None))
            )
        )
        resident = result.scalar_one_or_none()

        if not resident:
            await update.message.reply_text(f"User is not a resident.")
            return

        # End residency
        resident.end_date = datetime.utcnow()
        await session.commit()

    name = tg_user.first_name
    if tg_user.username:
        name = f"@{tg_user.username}"

    await update.message.reply_text(f"✅ {name} is no longer a resident.")
