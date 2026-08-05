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
async def test_list_anon_eligible_ids_includes_residents_and_members(
    session, settings
):
    service = UserService(session, settings)
    await service.ensure_user(1001, "resident")
    await service.ensure_user(2002, "member")
    await service.ensure_user(3003, "guest")
    await service.set_tier(1001, 2002, UserTier.member)

    assert set(await service.list_anon_eligible_ids()) == {1001, 2002}


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


@pytest.mark.asyncio
async def test_get_user_by_username_is_case_insensitive(session, settings):
    service = UserService(session, settings)
    await service.ensure_user(6006, "MixedCase")

    user = await service.get_user_by_username("@mixedcase")

    assert user is not None
    assert user.telegram_id == 6006


@pytest.mark.asyncio
async def test_set_tier_blocks_bootstrap_downgrade(session, settings):
    service = UserService(session, settings)

    await service.ensure_user(1001, "resident")
    updated = await service.set_tier(1001, 1001, UserTier.member)

    assert updated is False
    tier = await service.ensure_user(1001, "resident")
    assert tier == UserTier.resident
