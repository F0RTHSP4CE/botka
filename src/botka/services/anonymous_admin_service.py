from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import AnonymousAdminSnapshot, User, UserTier


class AnonymousAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_resident_ids(self) -> Sequence[int]:
        result = await self._session.execute(
            select(User.telegram_id).where(User.tier == UserTier.resident)
        )
        return result.scalars().all()

    async def get_snapshot(
        self, chat_id: int, telegram_id: int
    ) -> AnonymousAdminSnapshot | None:
        result = await self._session.execute(
            select(AnonymousAdminSnapshot).where(
                AnonymousAdminSnapshot.chat_id == chat_id,
                AnonymousAdminSnapshot.telegram_id == telegram_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_snapshots(
        self, chat_id: int
    ) -> Sequence[AnonymousAdminSnapshot]:
        result = await self._session.execute(
            select(AnonymousAdminSnapshot).where(
                AnonymousAdminSnapshot.chat_id == chat_id
            ).order_by(AnonymousAdminSnapshot.id)
        )
        return result.scalars().all()

    async def save_snapshot(
        self,
        chat_id: int,
        telegram_id: int,
        *,
        was_administrator: bool,
        permissions: dict[str, bool | None],
    ) -> AnonymousAdminSnapshot:
        snapshot = AnonymousAdminSnapshot(
            chat_id=chat_id,
            telegram_id=telegram_id,
            was_administrator=was_administrator,
            permissions=permissions,
        )
        self._session.add(snapshot)
        try:
            await self._session.commit()
        except IntegrityError:
            # Concurrent /anon calls must retain whichever original snapshot
            # reached the database first.
            await self._session.rollback()
            existing = await self.get_snapshot(chat_id, telegram_id)
            if existing is None:
                raise
            return existing
        return snapshot

    async def delete_snapshot(self, snapshot: AnonymousAdminSnapshot) -> None:
        await self._session.delete(snapshot)
        await self._session.commit()
