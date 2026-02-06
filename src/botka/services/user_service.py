from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from botka.config import Settings
from botka.db.models import User, UserTier


class UserService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def _get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self._session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_user(self, telegram_id: int) -> User | None:
        return await self._get_by_telegram_id(telegram_id)

    async def get_user_by_id(self, user_id: int) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def list_users_by_telegram_ids(
        self, telegram_ids: Iterable[int]
    ) -> Sequence[User]:
        ids = list(telegram_ids)
        if not ids:
            return []
        result = await self._session.execute(
            select(User).where(User.telegram_id.in_(ids))
        )
        return result.scalars().all()

    async def list_users_by_ids(self, user_ids: Iterable[int]) -> Sequence[User]:
        ids = list(user_ids)
        if not ids:
            return []
        result = await self._session.execute(select(User).where(User.id.in_(ids)))
        return result.scalars().all()

    async def _upsert(
        self, telegram_id: int, username: str | None, tier: UserTier
    ) -> User:
        user = await self._get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, username=username, tier=tier)
            self._session.add(user)
        else:
            user.username = username
            user.tier = tier
        await self._session.flush()
        await self._session.commit()
        return user

    async def _update_tier(self, telegram_id: int, tier: UserTier) -> None:
        await self._session.execute(
            update(User).where(User.telegram_id == telegram_id).values(tier=tier)
        )
        await self._session.commit()

    async def ensure_user(self, telegram_id: int, username: str | None) -> UserTier:
        tier = UserTier.guest
        if telegram_id in self._settings.bootstrap_resident_ids:
            tier = UserTier.resident
        user = await self._get_by_telegram_id(telegram_id)
        if user is not None:
            tier = user.tier
        await self._upsert(telegram_id, username, tier)
        return tier

    async def is_resident(self, telegram_id: int) -> bool:
        user = await self._get_by_telegram_id(telegram_id)
        return user is not None and user.tier == UserTier.resident

    def is_bootstrap_resident(self, telegram_id: int) -> bool:
        return telegram_id in self._settings.bootstrap_resident_ids

    async def set_tier(self, actor_id: int, target_id: int, tier: UserTier) -> bool:
        if not await self.is_resident(actor_id):
            return False
        if self.is_bootstrap_resident(target_id) and tier != UserTier.resident:
            return False
        user = await self._get_by_telegram_id(target_id)
        if user is None:
            await self._upsert(target_id, None, tier)
        else:
            await self._update_tier(target_id, tier)
        return True
