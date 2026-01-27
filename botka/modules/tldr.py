"""TLDR module - summarize chat discussions using LLM."""

import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from sqlalchemy import select, and_

from ..db import ChatHistory, TgUser, get_session

logger = logging.getLogger(__name__)


async def tldr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Summarize recent chat messages (TL;DR)."""
    from ..bot import state

    message = update.message
    chat_id = message.chat_id
    thread_id = message.message_thread_id

    openai = state.config.services.openai
    if not openai or not openai.api_key:
        await message.reply_text("OpenAI not configured.")
        return

    # Extract user query after command
    text = message.text or ""
    user_query = ""
    if " " in text:
        user_query = text.split(" ", 1)[1].strip()

    # Convert query to filter or use defaults
    if user_query:
        filter_data = await convert_query_to_filter(user_query)
    else:
        filter_data = {"time": None, "messages": 100}

    # Get chat history
    history = await get_chat_history(chat_id, thread_id)

    # Apply time filter
    if filter_data.get("time"):
        hours = filter_data["time"]
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        history = [
            h for h in history if h.timestamp.replace(tzinfo=timezone.utc) >= since
        ]

    # Reverse to get chronological order
    history.reverse()

    # Apply message count with hard cap of 500
    effective_limit = min(filter_data.get("messages") or 500, 500)
    if len(history) > effective_limit:
        history = history[-effective_limit:]

    if not history:
        await message.reply_text("No messages found for summarization.")
        return

    # Summarize messages
    summary = await summarize_messages(history)

    if summary:
        await message.reply_text(summary.strip())
    else:
        await message.reply_text("Failed to build summary.")


async def get_chat_history(chat_id: int, thread_id: Optional[int]) -> list:
    """Get chat history from database."""
    async with await get_session() as session:
        query = select(ChatHistory).where(ChatHistory.chat_id == chat_id)

        if thread_id:
            query = query.where(ChatHistory.topic_id == thread_id)

        query = query.order_by(ChatHistory.timestamp.desc()).limit(1000)

        result = await session.execute(query)
        return result.scalars().all()


async def convert_query_to_filter(query: str) -> dict:
    """Convert natural language query to filter using LLM."""
    from ..bot import state
    import httpx

    openai = state.config.services.openai
    if not openai:
        return {"time": None, "messages": 100}

    system_prompt = """You are a converter that transforms Russian or English natural language TLDR requests into a strict JSON filter with the following schema: {
  "time": <integer hours or null>,
  "messages": <integer or null>
}.
The field "time" represents how many hours back from now to include messages. 
The field "messages" represents how many last messages to include.
Return ONLY the JSON object with no leading or trailing explanation."""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{openai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": openai.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 20,
                    "temperature": 0.0,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "TldrFilter",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "time": {"type": ["integer", "null"]},
                                    "messages": {"type": ["integer", "null"]},
                                },
                                "required": ["time", "messages"],
                                "additionalProperties": False,
                            },
                        },
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to convert query to filter: {e}")
            return {"time": None, "messages": 100}


async def summarize_messages(history: list) -> Optional[str]:
    """Summarize messages using LLM."""
    from ..bot import state
    import httpx

    openai = state.config.services.openai
    nlp_config = state.config.nlp

    if not openai:
        return None

    # Build user map for names
    user_ids = list(set(h.from_user_id for h in history if h.from_user_id))
    user_map = {}

    if user_ids:
        async with await get_session() as session:
            result = await session.execute(
                select(TgUser).where(TgUser.id.in_(user_ids))
            )
            for user in result.scalars().all():
                if user.username:
                    user_map[user.id] = f"@{user.username}"
                else:
                    name = user.first_name
                    if user.last_name:
                        name += f" {user.last_name}"
                    user_map[user.id] = name

    # Build transcript
    lines = []
    for entry in history:
        if not entry.message_text:
            continue
        prefix = (
            user_map.get(entry.from_user_id, "Unknown")
            if entry.from_user_id
            else "Unknown"
        )
        lines.append(f"{prefix}: {entry.message_text}")

    transcript = "\n".join(lines)

    if not transcript.strip():
        return None

    system_prompt = "You are an assistant that produces a concise TL;DR summary (in the same language as the messages) of the following Telegram thread messages. Focus on the key discussion points and decisions. Return no more than 10 sentences. Mention the most important points and decisions. Do not mention the user names with @."

    # Use NLP model if configured, otherwise default
    model = openai.model
    if nlp_config and nlp_config.models:
        model = nlp_config.models[-1]  # Use last (most capable) model

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{openai.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": transcript},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.3,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Failed to summarize messages: {e}")
            return None


def get_handlers():
    """Get handlers for tldr module."""
    return [
        CommandHandler("tldr", tldr_cmd),
    ]
