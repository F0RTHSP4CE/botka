"""Memory management for NLP module."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from telegram import Message
from sqlalchemy import select, and_, or_

from ...db import ChatHistory, Memory, get_session
from .types import SaveMemoryArgs, NlpDebug

logger = logging.getLogger(__name__)

# General thread ID constant
GENERAL_THREAD_ID = 1


async def get_chat_history(
    chat_id: int, thread_id: Optional[int], max_history: int = 100
) -> List[ChatHistory]:
    """Retrieve chat history for the given chat/thread."""
    thread_id = thread_id or GENERAL_THREAD_ID
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)

    async with await get_session() as session:
        result = await session.execute(
            select(ChatHistory)
            .where(
                and_(
                    ChatHistory.chat_id == chat_id,
                    ChatHistory.topic_id == thread_id,
                    ChatHistory.timestamp >= day_ago,
                )
            )
            .order_by(ChatHistory.timestamp.desc())
            .limit(max_history)
        )
        return list(result.scalars().all())


async def get_relevant_memories(
    chat_id: int, thread_id: Optional[int], user_id: int
) -> List[Memory]:
    """Get relevant memories (active and recently expired)."""
    thread_id = thread_id or GENERAL_THREAD_ID
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    async with await get_session() as session:
        # Fetch all memories that are either active, have null expiration, or expired within the last day
        result = await session.execute(
            select(Memory).where(
                or_(
                    Memory.expiration_date.is_(None),
                    Memory.expiration_date > yesterday,
                )
            )
        )
        all_memories = result.scalars().all()

        # Filter by chat/thread/user specificity
        filtered = []
        for memory in all_memories:
            if memory.chat_id is not None and memory.chat_id != chat_id:
                continue
            if memory.thread_id is not None and memory.thread_id != thread_id:
                continue
            if memory.user_id is not None and memory.user_id != user_id:
                continue
            filtered.append(memory)

        return filtered


async def store_message(msg: Message) -> None:
    """Store a message in chat history."""
    text = msg.text or msg.caption
    if not text:
        return

    # Skip commands and messages starting with --
    if text.startswith("/") or text.startswith("--"):
        return

    thread_id = msg.message_thread_id or GENERAL_THREAD_ID

    async with await get_session() as session:
        entry = ChatHistory(
            chat_id=msg.chat_id,
            topic_id=thread_id,
            message_id=msg.message_id,
            from_user_id=msg.from_user.id if msg.from_user else None,
            timestamp=datetime.now(timezone.utc),
            message_text=text,
        )
        session.add(entry)
        await session.commit()


async def store_bot_response(
    original_msg: Message,
    sent_msg: Message,
    content: str,
    nlp_debug: NlpDebug,
) -> None:
    """Store bot's response in chat history."""
    thread_id = original_msg.message_thread_id or GENERAL_THREAD_ID

    async with await get_session() as session:
        entry = ChatHistory(
            chat_id=original_msg.chat_id,
            topic_id=thread_id,
            message_id=sent_msg.message_id,
            from_user_id=None,  # From bot
            timestamp=datetime.now(timezone.utc),
            message_text=content,
            classification_result=nlp_debug.classification_result,
            used_model=nlp_debug.used_model,
        )
        session.add(entry)
        await session.commit()


async def handle_save_memory(
    args: SaveMemoryArgs,
    chat_id: int,
    thread_id: Optional[int],
    user_id: int,
) -> str:
    """Save a new memory."""
    thread_id = thread_id or GENERAL_THREAD_ID

    # Calculate expiration date
    expiration_date = None
    if args.duration_hours is not None:
        expiration_date = datetime.now(timezone.utc) + timedelta(
            hours=args.duration_hours
        )

    async with await get_session() as session:
        memory = Memory(
            content=args.memory_text,
            expiration_date=expiration_date,
            chat_id=chat_id if args.chat_specific else None,
            thread_id=thread_id if args.thread_specific else None,
            user_id=user_id if args.user_specific else None,
            created_at=datetime.now(timezone.utc),
        )
        session.add(memory)
        await session.commit()

        return f"Memory saved with ID {memory.id}."


async def handle_remove_memory(memory_id: int) -> str:
    """Remove a memory by ID."""
    async with await get_session() as session:
        result = await session.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()

        if memory:
            await session.delete(memory)
            await session.commit()
            return f"Memory {memory_id} removed."
        else:
            return f"Memory {memory_id} not found."
