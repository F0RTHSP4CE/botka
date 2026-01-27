"""NLP message processing."""

import logging
import json
from typing import Optional, Dict, Any, List

import httpx
from telegram import Update, Message
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from sqlalchemy import select

from ...db import TgUser, NeededItem, get_session
from .types import (
    NlpDebug,
    SaveMemoryArgs,
    CHAT_TOOLS,
    SYSTEM_PROMPT,
    ClassificationResult,
)
from .classification import classify_request
from .memory import (
    get_chat_history,
    get_relevant_memories,
    store_bot_response,
    handle_save_memory,
    handle_remove_memory,
    GENERAL_THREAD_ID,
)

logger = logging.getLogger(__name__)


async def handle_nlp_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process a message with NLP."""
    from ...bot import state, is_resident

    msg = update.message
    text = msg.text or msg.caption
    if not text:
        return

    nlp_config = state.config.nlp
    openai = state.config.services.openai

    if not nlp_config or not nlp_config.enabled:
        return

    if not openai or not openai.api_key:
        return

    chat_id = msg.chat_id
    thread_id = msg.message_thread_id
    user_id = msg.from_user.id if msg.from_user else 0

    # Get chat history for context
    history = await get_chat_history(chat_id, thread_id, nlp_config.max_history)
    history.reverse()  # Chronological order

    # Build history context string
    user_map = await get_user_map([h.from_user_id for h in history if h.from_user_id])
    history_context = build_history_context(history, user_map)

    # Classify the request
    classification = await classify_request(text, history_context)

    if classification == ClassificationResult.IGNORE:
        logger.debug(f"Message ignored by classification: {text[:50]}")
        return

    # Select model based on classification
    model_index = (
        min(classification.value - 1, len(nlp_config.models) - 1)
        if nlp_config.models
        else 0
    )
    model = nlp_config.models[model_index] if nlp_config.models else openai.model

    nlp_debug = NlpDebug(
        classification_result=f"HANDLE_{classification.value}",
        used_model=model,
    )

    # Send typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Get relevant memories
    memories = await get_relevant_memories(chat_id, thread_id, user_id)

    # Build messages for LLM
    messages = build_llm_messages(
        text=text,
        history=history,
        user_map=user_map,
        memories=memories,
        from_user=msg.from_user,
    )

    # Process with LLM (potentially with tool calls)
    response_text = await process_with_llm(
        messages=messages,
        model=model,
        chat_id=chat_id,
        thread_id=thread_id,
        user_id=user_id,
        is_resident_user=await is_resident(user_id),
    )

    if response_text:
        # Split long messages
        for part in split_long_message(response_text):
            sent_msg = await msg.reply_text(part)

        # Store bot response
        await store_bot_response(msg, sent_msg, response_text, nlp_debug)


async def get_user_map(user_ids: List[Optional[int]]) -> Dict[int, str]:
    """Get mapping of user IDs to display names."""
    user_ids = [uid for uid in user_ids if uid is not None]
    if not user_ids:
        return {}

    async with await get_session() as session:
        result = await session.execute(select(TgUser).where(TgUser.id.in_(user_ids)))
        user_map = {}
        for user in result.scalars().all():
            if user.username:
                user_map[user.id] = f"@{user.username}"
            else:
                name = user.first_name
                if user.last_name:
                    name += f" {user.last_name}"
                user_map[user.id] = name
        return user_map


def build_history_context(history: list, user_map: Dict[int, str]) -> str:
    """Build context string from chat history."""
    lines = []
    for entry in history[-20:]:  # Last 20 messages for context
        if not entry.message_text:
            continue
        prefix = (
            user_map.get(entry.from_user_id, "Unknown") if entry.from_user_id else "Bot"
        )
        lines.append(f"{prefix}: {entry.message_text}")
    return "\n".join(lines)


def build_llm_messages(
    text: str,
    history: list,
    user_map: Dict[int, str],
    memories: list,
    from_user,
) -> List[Dict[str, Any]]:
    """Build messages array for LLM request."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add memories context
    if memories:
        memory_text = "Active memories:\n"
        for m in memories:
            exp_str = (
                f" (expires: {m.expiration_date})"
                if m.expiration_date
                else " (persistent)"
            )
            memory_text += f"- [ID {m.id}] {m.content}{exp_str}\n"
        messages.append({"role": "system", "content": memory_text})

    # Add recent history as context
    for entry in history[-10:]:  # Last 10 messages
        if not entry.message_text:
            continue
        prefix = (
            user_map.get(entry.from_user_id, "Unknown")
            if entry.from_user_id
            else "Assistant"
        )
        role = "assistant" if entry.from_user_id is None else "user"
        content = (
            entry.message_text
            if role == "assistant"
            else f"{prefix}: {entry.message_text}"
        )
        messages.append({"role": role, "content": content})

    # Add current message
    username = "Unknown"
    if from_user:
        username = from_user.username or from_user.first_name or "Unknown"
    messages.append({"role": "user", "content": f"{username}: {text}"})

    return messages


async def process_with_llm(
    messages: List[Dict[str, Any]],
    model: str,
    chat_id: int,
    thread_id: Optional[int],
    user_id: int,
    is_resident_user: bool,
    max_tool_rounds: int = 5,
) -> Optional[str]:
    """Process messages with LLM, handling tool calls."""
    from ...bot import state

    openai = state.config.services.openai
    if not openai:
        return None

    current_messages = messages.copy()

    for _ in range(max_tool_rounds):
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
                        "messages": current_messages,
                        "tools": CHAT_TOOLS,
                        "tool_choice": "auto",
                        "max_tokens": 1000,
                        "temperature": 0.7,
                    },
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

                choice = data["choices"][0]
                message = choice["message"]

                # Check for tool calls
                tool_calls = message.get("tool_calls", [])

                if not tool_calls:
                    # No tool calls, return content
                    return message.get("content", "")

                # Add assistant message with tool calls
                current_messages.append(message)

                # Process each tool call
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    func_args = json.loads(tool_call["function"]["arguments"])

                    result = await execute_tool(
                        func_name,
                        func_args,
                        chat_id,
                        thread_id,
                        user_id,
                        is_resident_user,
                    )

                    current_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        }
                    )

            except Exception as e:
                logger.error(f"LLM processing error: {e}")
                return None

    return "Request required too many steps to process."


async def execute_tool(
    name: str,
    args: Dict[str, Any],
    chat_id: int,
    thread_id: Optional[int],
    user_id: int,
    is_resident_user: bool,
) -> str:
    """Execute a tool and return the result."""
    from ...bot import state
    from ..needs import get_needs_list, add_need_item
    from ..butler import do_open_door

    try:
        if name == "status":
            return await handle_status_command()

        elif name == "needs":
            items = await get_needs_list()
            if items:
                return "Shopping list:\n" + "\n".join(f"- {item}" for item in items)
            return "Shopping list is empty."

        elif name == "add_need":
            item = args.get("item", "")
            if item:
                await add_need_item(item, user_id)
                return f"Added '{item}' to shopping list."
            return "No item specified."

        elif name == "open_door":
            if not is_resident_user:
                return "Only residents can open the door."
            success = await do_open_door()
            return "Door opened successfully." if success else "Failed to open door."

        elif name == "save_memory":
            save_args = SaveMemoryArgs(
                memory_text=args.get("memory_text", ""),
                duration_hours=args.get("duration_hours"),
                chat_specific=args.get("chat_specific", False),
                thread_specific=args.get("thread_specific", False),
                user_specific=args.get("user_specific", False),
            )
            return await handle_save_memory(save_args, chat_id, thread_id, user_id)

        elif name == "remove_memory":
            memory_id = args.get("memory_id", 0)
            return await handle_remove_memory(memory_id)

        elif name == "search":
            query = args.get("query", "")
            return await handle_search(query)

        else:
            return f"Unknown function: {name}"

    except Exception as e:
        logger.error(f"Tool execution error ({name}): {e}")
        return f"Error executing {name}: {str(e)}"


async def handle_status_command() -> str:
    """Handle status command for NLP."""
    from ...bot import state

    active_users = state.active_users
    if not active_users:
        return "No one is currently in the hackerspace."

    # Get user names
    user_map = await get_user_map(list(active_users))
    names = [user_map.get(uid, f"User {uid}") for uid in active_users]

    count = len(names)
    if count == 1:
        return f"There is 1 resident in the hackerspace: {names[0]}."
    return f"There are {count} residents in the hackerspace: {', '.join(names)}."


async def handle_search(query: str) -> str:
    """Handle search command."""
    # Simplified search - could integrate with wiki.js or web search
    return f"Search for '{query}' is not fully implemented. Please use the wiki at wiki.f0rth.space."


def split_long_message(text: str, max_length: int = 4096) -> List[str]:
    """Split a long message into parts."""
    if len(text) <= max_length:
        return [text]

    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break

        # Find a good split point
        split_point = text.rfind("\n", 0, max_length)
        if split_point == -1:
            split_point = text.rfind(" ", 0, max_length)
        if split_point == -1:
            split_point = max_length

        parts.append(text[:split_point])
        text = text[split_point:].lstrip()

    return parts
