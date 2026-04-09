from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import AgendaTopic


class MeetingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_topic(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> AgendaTopic:
        topic = AgendaTopic(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.commit()
        return topic

    async def set_reply_message_id(self, topic_id: int, reply_message_id: int) -> None:
        await self._session.execute(
            update(AgendaTopic)
            .where(AgendaTopic.id == topic_id)
            .values(bot_reply_message_id=reply_message_id)
        )
        await self._session.commit()

    async def set_notify_message_id(
        self, topic_id: int, notify_message_id: int
    ) -> None:
        await self._session.execute(
            update(AgendaTopic)
            .where(AgendaTopic.id == topic_id)
            .values(notify_message_id=notify_message_id)
        )
        await self._session.commit()

    async def update_topic_by_message(
        self, chat_id: int, message_id: int, new_text: str
    ) -> AgendaTopic | None:
        result = await self._session.execute(
            select(AgendaTopic).where(
                AgendaTopic.chat_id == chat_id,
                AgendaTopic.message_id == message_id,
                AgendaTopic.cancelled == False,
            )
        )
        topic = result.scalar_one_or_none()
        if topic is None:
            return None
        topic.text = new_text
        await self._session.commit()
        return topic

    async def cancel_topic_by_message(
        self, chat_id: int, message_id: int
    ) -> AgendaTopic | None:
        result = await self._session.execute(
            select(AgendaTopic).where(
                AgendaTopic.chat_id == chat_id,
                AgendaTopic.message_id == message_id,
                AgendaTopic.cancelled == False,
            )
        )
        topic = result.scalar_one_or_none()
        if topic is None:
            return None
        topic.cancelled = True
        await self._session.commit()
        return topic

    async def cancel_topic_by_reply_message(
        self, chat_id: int, reply_message_id: int
    ) -> AgendaTopic | None:
        result = await self._session.execute(
            select(AgendaTopic).where(
                AgendaTopic.chat_id == chat_id,
                AgendaTopic.bot_reply_message_id == reply_message_id,
                AgendaTopic.cancelled == False,
            )
        )
        return result.scalar_one_or_none()

    async def cancel_topic_by_id(self, topic_id: int) -> AgendaTopic | None:
        result = await self._session.execute(
            select(AgendaTopic).where(
                AgendaTopic.id == topic_id,
                AgendaTopic.cancelled == False,
            )
        )
        topic = result.scalar_one_or_none()
        if topic is None:
            return None
        topic.cancelled = True
        await self._session.commit()
        return topic

    async def get_topics_since(self, since: datetime) -> list[AgendaTopic]:
        result = await self._session.execute(
            select(AgendaTopic)
            .where(
                AgendaTopic.cancelled == False,
                AgendaTopic.created_at >= since,
            )
            .order_by(AgendaTopic.created_at)
        )
        return list(result.scalars().all())
