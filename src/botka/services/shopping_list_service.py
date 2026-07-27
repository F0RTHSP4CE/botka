from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import ShoppingItem

_PENDING_TTL = 30.0  # seconds


class ShoppingBuyConfirmationTracker:
    """APP-scoped in-memory tracker for double-click buy confirmations."""

    def __init__(self) -> None:
        self._pending: dict[tuple[int, int], float] = {}  # (item_id, user_id) -> monotonic ts

    def set_pending(self, item_id: int, user_id: int) -> None:
        self._pending[(item_id, user_id)] = time.monotonic()

    def check_and_clear(self, item_id: int, user_id: int) -> bool:
        """Return True and clear the pending state if a valid pending confirmation exists."""
        key = (item_id, user_id)
        ts = self._pending.get(key)
        if ts is None:
            return False
        del self._pending[key]
        if time.monotonic() - ts > _PENDING_TTL:
            return False
        return True


class ShoppingListService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_item(self, actor_telegram_id: int, text: str) -> None:
        await self.add_items(actor_telegram_id, (text,))

    async def add_items(self, actor_telegram_id: int, items: Iterable[str]) -> int:
        pending = [
            ShoppingItem(
                created_by_telegram_id=actor_telegram_id,
                text=text,
                bought=False,
            )
            for text in items
        ]
        self._session.add_all(pending)
        await self._session.commit()
        return len(pending)

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

    @staticmethod
    def extract_dash_items(text: str) -> list[str]:
        items: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\s*-\s+(.+)$", line)
            if match:
                items.append(match.group(1).strip())
        return items
