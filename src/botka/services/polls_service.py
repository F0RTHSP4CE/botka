from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import Poll, PollAudience, PollVote, User, UserTier


class PollsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_poll(
        self,
        *,
        poll_id: str,
        chat_id: int,
        message_id: int,
        author_telegram_id: int,
        question: str,
        audience: PollAudience,
        awaiting_message_id: int | None,
        closes_at: datetime | None = None,
    ) -> Poll:
        poll = Poll(
            poll_id=poll_id,
            chat_id=chat_id,
            message_id=message_id,
            author_telegram_id=author_telegram_id,
            question=question,
            audience=audience,
            awaiting_message_id=awaiting_message_id,
            closes_at=closes_at or datetime.now(timezone.utc) + timedelta(days=7),
            closed=False,
        )
        self._session.add(poll)
        await self._session.flush()
        await self._session.commit()
        return poll

    async def get_poll(self, poll_id: str) -> Poll | None:
        result = await self._session.execute(
            select(Poll).where(Poll.poll_id == poll_id)
        )
        return result.scalar_one_or_none()

    async def set_awaiting_message_id(self, poll_id: str, message_id: int) -> None:
        await self._session.execute(
            update(Poll)
            .where(Poll.poll_id == poll_id)
            .values(awaiting_message_id=message_id)
        )
        await self._session.commit()

    async def mark_closed(self, poll_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self._session.execute(
            update(Poll)
            .where(Poll.poll_id == poll_id)
            .values(closed=True, closed_at=now)
        )
        await self._session.commit()

    async def list_due_polls(self, now: datetime) -> Sequence[Poll]:
        result = await self._session.execute(
            select(Poll).where(Poll.closed.is_(False), Poll.closes_at <= now)
        )
        return result.scalars().all()

    async def list_voted_user_ids(self, poll_id: str) -> set[int]:
        result = await self._session.execute(
            select(PollVote.user_telegram_id).where(PollVote.poll_id == poll_id)
        )
        return {row[0] for row in result.all()}

    async def set_vote(self, poll_id: str, user_telegram_id: int, voted: bool) -> None:
        if voted:
            result = await self._session.execute(
                select(PollVote).where(
                    PollVote.poll_id == poll_id,
                    PollVote.user_telegram_id == user_telegram_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                self._session.add(
                    PollVote(poll_id=poll_id, user_telegram_id=user_telegram_id)
                )
            await self._session.commit()
            return
        await self._session.execute(
            delete(PollVote).where(
                PollVote.poll_id == poll_id,
                PollVote.user_telegram_id == user_telegram_id,
            )
        )
        await self._session.commit()

    async def list_target_users(self, audience: PollAudience) -> Sequence[User]:
        tiers = self._audience_tiers(audience)
        result = await self._session.execute(select(User).where(User.tier.in_(tiers)))
        return result.scalars().all()

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

    def _audience_tiers(self, audience: PollAudience) -> Iterable[UserTier]:
        if audience == PollAudience.residents:
            return [UserTier.resident]
        if audience == PollAudience.members:
            return [UserTier.member]
        if audience in (PollAudience.everyone, PollAudience.all, PollAudience.anyone):
            return [UserTier.member, UserTier.resident]
        return [UserTier.resident, UserTier.member]
