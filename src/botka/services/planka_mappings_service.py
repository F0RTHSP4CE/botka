from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import PlankaCardMapping


class PlankaCardMappingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_short_id(self, planka_card_id: str) -> int:
        result = await self._session.execute(
            select(PlankaCardMapping).where(PlankaCardMapping.planka_card_id == planka_card_id)
        )
        mapping = result.scalar_one_or_none()
        if mapping is None:
            mapping = PlankaCardMapping(planka_card_id=planka_card_id)
            self._session.add(mapping)
            await self._session.flush()
            await self._session.commit()
        return mapping.short_id

    async def resolve_card_id(self, short_id_or_long: str) -> str | None:
        candidate = short_id_or_long.strip()
        if not candidate:
            return None
        # Long Planka IDs are snowflake-like numerics (16+ digits)
        if candidate.isdigit() and len(candidate) >= 16:
            return candidate
        if not candidate.isdigit():
            return None
        result = await self._session.execute(
            select(PlankaCardMapping).where(PlankaCardMapping.short_id == int(candidate))
        )
        mapping = result.scalar_one_or_none()
        return mapping.planka_card_id if mapping else None

