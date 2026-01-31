from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import ShoppingItem, ShoppingNeedsPin


class ShoppingListService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _add_item(self, actor_telegram_id: int, text: str) -> ShoppingItem:
        item = ShoppingItem(
            created_by_telegram_id=actor_telegram_id,
            text=text,
            bought=False,
        )
        self._session.add(item)
        await self._session.flush()
        await self._session.commit()
        return item

    async def add_item(self, actor_telegram_id: int, text: str) -> None:
        await self._add_item(actor_telegram_id, text)

    async def add_items(self, actor_telegram_id: int, items: Iterable[str]) -> int:
        count = 0
        for item in items:
            await self._add_item(actor_telegram_id, item)
            count += 1
        return count

    async def list_open_items(self) -> Sequence[ShoppingItem]:
        result = await self._session.execute(
            select(ShoppingItem)
            .where(
                ShoppingItem.bought.is_(False),
            )
            .order_by(ShoppingItem.id.asc())
        )
        return result.scalars().all()

    async def mark_bought(self, item_id: int) -> ShoppingItem | None:
        item = await self._session.get(ShoppingItem, item_id)
        if item is None:
            return None
        item.bought = True
        await self._session.commit()
        return item

    async def get_needs_message_id(self, chat_id: int, topic_id: int) -> int | None:
        result = await self._session.execute(
            select(ShoppingNeedsPin).where(
                ShoppingNeedsPin.chat_id == chat_id,
                ShoppingNeedsPin.topic_id == topic_id,
            )
        )
        row = result.scalar_one_or_none()
        return row.message_id if row else None

    async def set_needs_message_id(
        self,
        chat_id: int,
        topic_id: int,
        message_id: int,
    ) -> None:
        result = await self._session.execute(
            select(ShoppingNeedsPin).where(
                ShoppingNeedsPin.chat_id == chat_id,
                ShoppingNeedsPin.topic_id == topic_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ShoppingNeedsPin(
                chat_id=chat_id,
                topic_id=topic_id,
                message_id=message_id,
            )
            self._session.add(row)
        else:
            row.message_id = message_id
        await self._session.commit()

    async def clear_needs_message_id(self, chat_id: int, topic_id: int) -> None:
        result = await self._session.execute(
            select(ShoppingNeedsPin).where(
                ShoppingNeedsPin.chat_id == chat_id,
                ShoppingNeedsPin.topic_id == topic_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        await self._session.delete(row)
        await self._session.commit()

    def extract_dash_items(self, text: str) -> list[str]:
        items: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\s*-\s+(.+)$", line)
            if match:
                items.append(match.group(1).strip())
        return items
