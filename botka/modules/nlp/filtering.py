"""Message filtering for NLP module."""

import logging
import random
from typing import Optional

from telegram import Update, Message
from telegram.ext import ContextTypes, MessageHandler, filters

from .classification import classify_random_request
from .memory import get_chat_history, store_message, GENERAL_THREAD_ID
from .processing import handle_nlp_message

logger = logging.getLogger(__name__)


def should_process_nlp(msg: Message) -> bool:
    """Check if message should be processed with NLP."""
    from ...bot import state

    nlp_config = state.config.nlp
    if not nlp_config or not nlp_config.enabled:
        return False

    # Skip forwarded messages
    if msg.forward_date:
        return False

    text = msg.text or msg.caption
    if not text:
        return False

    # Skip messages starting with -- or /
    if text.startswith("--") or text.startswith("/"):
        return False

    # Always process private messages
    if msg.chat.type == "private":
        return True

    # Skip in passive mode
    if state.config.telegram.passive_mode:
        return False

    # Check if replying to bot
    if msg.reply_to_message:
        reply_user = msg.reply_to_message.from_user
        if reply_user and reply_user.is_bot:
            # Check if it's our bot (would need bot_id)
            return True

    # Check trigger words
    trigger_words = nlp_config.trigger_words
    if not trigger_words:
        return True  # No trigger words means process all

    # Normalize text words
    text_words = set(word.strip(".,!?;:'\"").lower() for word in text.split())

    # Check if any trigger word matches
    for trigger in trigger_words:
        if trigger.lower() in text_words:
            return True

    return False


async def filter_and_process(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Filter and process messages for NLP."""
    msg = update.message
    if not msg:
        return

    # Store message in history
    await store_message(msg)

    # Check if should process with NLP
    if should_process_nlp(msg):
        await handle_nlp_message(update, context)


async def random_filter_and_process(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Random filtering for casual interventions."""
    from ...bot import state

    msg = update.message
    if not msg:
        return

    nlp_config = state.config.nlp
    if not nlp_config or not nlp_config.enabled:
        return

    # Skip in passive mode
    if state.config.telegram.passive_mode:
        return

    # Check random chance
    random_chance = nlp_config.random_answer_probability
    if random_chance <= 0:
        return

    if random.random() * 100 > random_chance:
        return

    text = msg.text or msg.caption
    if not text:
        return

    # Skip special prefixes
    if text.startswith("--") or text.startswith("/"):
        return

    # Get history context for classification
    history = await get_chat_history(msg.chat_id, msg.message_thread_id, 20)
    history.reverse()
    history_context = "\n".join(h.message_text for h in history[-10:] if h.message_text)

    # Classify if should respond
    should_respond = await classify_random_request(text, history_context)

    if should_respond:
        await handle_nlp_message(update, context)


def get_message_handlers():
    """Get handlers for NLP module."""
    return [
        # Main NLP handler (processes triggered messages)
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            filter_and_process,
        ),
    ]
