from __future__ import annotations

import pytest

from botka.db.models import UserTier
from botka.services.user_service import UserService


@pytest.mark.asyncio
async def test_ensure_user_bootstrap_resident(session, settings):
    service = UserService(session, settings)

    tier = await service.ensure_user(1001, "resident_user")

    assert tier == UserTier.resident
    assert await service.is_resident(1001) is True


@pytest.mark.asyncio
async def test_ensure_user_preserves_existing_tier(session, settings):
    service = UserService(session, settings)

    await service.ensure_user(1001, "resident_user")
    await service.ensure_user(2002, "member_user")
    await service.set_tier(1001, 2002, UserTier.member)

    tier = await service.ensure_user(2002, "member_user")

    assert tier == UserTier.member


@pytest.mark.asyncio
async def test_set_tier_requires_resident(session, settings):
    service = UserService(session, settings)

    await service.ensure_user(3003, "guest")
    updated = await service.set_tier(3003, 4004, UserTier.member)

    assert updated is False


@pytest.mark.asyncio
async def test_set_tier_creates_user_when_missing(session, settings):
    service = UserService(session, settings)

    await service.ensure_user(1001, "resident")
    updated = await service.set_tier(1001, 5005, UserTier.member)

    assert updated is True
    assert await service.is_resident(1001) is True
    tier = await service.ensure_user(5005, "member")
    assert tier == UserTier.member
