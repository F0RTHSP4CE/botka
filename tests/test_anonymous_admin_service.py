from __future__ import annotations

import pytest

from botka.db.models import User, UserTier
from botka.services.anonymous_admin_service import AnonymousAdminService


@pytest.mark.asyncio
async def test_anonymous_admin_snapshots_are_scoped_by_chat(session) -> None:
    session.add_all(
        [
            User(telegram_id=1, tier=UserTier.resident),
            User(telegram_id=2, tier=UserTier.member),
            User(telegram_id=3, tier=UserTier.resident),
        ]
    )
    await session.commit()
    service = AnonymousAdminService(session)

    assert set(await service.list_resident_ids()) == {1, 3}

    first = await service.save_snapshot(
        -1001,
        1,
        was_administrator=False,
        permissions={},
    )
    await service.save_snapshot(
        -1002,
        1,
        was_administrator=True,
        permissions={"is_anonymous": False, "can_manage_chat": True},
    )

    assert await service.get_snapshot(-1001, 1) is first
    assert [item.telegram_id for item in await service.list_snapshots(-1001)] == [1]

    await service.delete_snapshot(first)

    assert await service.get_snapshot(-1001, 1) is None
    assert len(await service.list_snapshots(-1002)) == 1
