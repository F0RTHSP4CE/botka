from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import BorrowedItem


class BorrowedItemsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_item(
        self,
        actor_telegram_id: int,
        chat_id: int,
        message_id: int,
        item_name: str,
    ) -> BorrowedItem:
        item = BorrowedItem(
            created_by_telegram_id=actor_telegram_id,
            chat_id=chat_id,
            message_id=message_id,
            item_name=item_name,
            returned=False,
        )
        self._session.add(item)
        await self._session.flush()
        await self._session.commit()
        return item

    async def list_open_items(self) -> Sequence[BorrowedItem]:
        result = await self._session.execute(
            select(BorrowedItem)
            .where(BorrowedItem.returned.is_(False))
            .order_by(BorrowedItem.id.asc())
        )
        return result.scalars().all()

    async def list_items_for_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> Sequence[BorrowedItem]:
        result = await self._session.execute(
            select(BorrowedItem)
            .where(BorrowedItem.chat_id == chat_id)
            .where(BorrowedItem.message_id == message_id)
            .order_by(BorrowedItem.id.asc())
        )
        return result.scalars().all()

    async def has_open_items_for_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        result = await self._session.execute(
            select(BorrowedItem.id)
            .where(BorrowedItem.chat_id == chat_id)
            .where(BorrowedItem.message_id == message_id)
            .where(BorrowedItem.returned.is_(False))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_item(self, item_id: int) -> BorrowedItem | None:
        return await self._session.get(BorrowedItem, item_id)

    async def mark_returned(self, item_id: int) -> BorrowedItem | None:
        item = await self._session.get(BorrowedItem, item_id)
        if item is None:
            return None
        if not item.returned:
            item.returned = True
            item.returned_at = datetime.now(timezone.utc)
            await self._session.commit()
        return item

    async def mark_unreturned(self, item_id: int) -> BorrowedItem | None:
        item = await self._session.get(BorrowedItem, item_id)
        if item is None:
            return None
        if item.returned:
            item.returned = False
            item.returned_at = None
            await self._session.commit()
        return item
