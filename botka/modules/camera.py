"""Camera module for fetching and displaying camera images."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..services import get_camera_image

logger = logging.getLogger(__name__)


async def cmd_racovina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show racovina camera image."""
    from ..bot import state, is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not state.config.services.racovina_cam:
        await update.message.reply_text("Racovina camera is not configured.")
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


async def cmd_hlam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show hlam/vortex camera image."""
    from ..bot import state, is_resident

    user = update.effective_user
    if not await is_resident(user.id):
        await update.message.reply_text("This command is only available to residents.")
        return

    if not state.config.services.vortex_of_doom_cam:
        await update.message.reply_text("Hlam camera is not configured.")
        return

    await update.message.chat.send_action("upload_photo")

    image = await get_camera_image(
        state.http_client,
        state.config.services.vortex_of_doom_cam,
    )

    if image:
        await update.message.reply_photo(image)
    else:
        await update.message.reply_text("Failed to fetch camera image.")
