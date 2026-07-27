from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import PlankaCardMapping


class PlankaCardMappingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_short_id(self, planka_card_id: str) -> int:
        return (await self.get_or_create_short_ids((planka_card_id,)))[
            planka_card_id
        ]

    async def get_or_create_short_ids(
        self, planka_card_ids: Sequence[str]
    ) -> dict[str, int]:
        card_ids = tuple(dict.fromkeys(planka_card_ids))
        if not card_ids:
            return {}

        mappings = await self._get_mappings(card_ids)
        while missing := [card_id for card_id in card_ids if card_id not in mappings]:
            created = [
                PlankaCardMapping(planka_card_id=card_id) for card_id in missing
            ]
            self._session.add_all(created)
            try:
                await self._session.commit()
            except IntegrityError:
                # A concurrent request may have inserted part of this batch.
                await self._session.rollback()
                refreshed = await self._get_mappings(card_ids)
                if refreshed.keys() <= mappings.keys():
                    raise
                mappings = refreshed
            else:
                mappings.update(
                    (mapping.planka_card_id, mapping) for mapping in created
                )
        return {card_id: mappings[card_id].short_id for card_id in card_ids}

    async def _get_mappings(
        self, planka_card_ids: Sequence[str]
    ) -> dict[str, PlankaCardMapping]:
        result = await self._session.execute(
            select(PlankaCardMapping).where(
                PlankaCardMapping.planka_card_id.in_(planka_card_ids)
            )
        )
        return {
            mapping.planka_card_id: mapping for mapping in result.scalars()
        }

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
