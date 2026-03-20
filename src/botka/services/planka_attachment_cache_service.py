from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import PlankaAttachmentTelegramCache


class PlankaAttachmentCacheService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_file_id(self, planka_attachment_id: str) -> str | None:
        result = await self._session.execute(
            select(PlankaAttachmentTelegramCache).where(
                PlankaAttachmentTelegramCache.planka_attachment_id == planka_attachment_id
            )
        )
        row = result.scalar_one_or_none()
        return row.telegram_file_id if row else None

    async def set_file_id(self, planka_attachment_id: str, telegram_file_id: str) -> None:
        result = await self._session.execute(
            select(PlankaAttachmentTelegramCache).where(
                PlankaAttachmentTelegramCache.planka_attachment_id == planka_attachment_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = PlankaAttachmentTelegramCache(
                planka_attachment_id=planka_attachment_id,
                telegram_file_id=telegram_file_id,
            )
            self._session.add(row)
        else:
            row.telegram_file_id = telegram_file_id
        await self._session.flush()
        await self._session.commit()

    async def clear_file_id(self, planka_attachment_id: str) -> None:
        result = await self._session.execute(
            select(PlankaAttachmentTelegramCache).where(
                PlankaAttachmentTelegramCache.planka_attachment_id == planka_attachment_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        await self._session.delete(row)
        await self._session.flush()
        await self._session.commit()
