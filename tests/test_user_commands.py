from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from botka.db.models import UserTier
from botka.handlers.users.commands import user_handler


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=1001),
        reply=AsyncMock(),
    )


async def test_user_tier_can_be_set_by_handle() -> None:
    message = _message()
    target = SimpleNamespace(telegram_id=2002, username="alice")
    user_service = SimpleNamespace(
        get_user_by_username=AsyncMock(return_value=target),
        is_bootstrap_resident=Mock(return_value=False),
        set_tier=AsyncMock(return_value=True),
        get_user=AsyncMock(return_value=target),
    )

    await user_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args="resident @ALICE"),
        user_service,
    )

    user_service.get_user_by_username.assert_awaited_once_with("@ALICE")
    user_service.set_tier.assert_awaited_once_with(
        1001,
        2002,
        UserTier.resident,
    )
    message.reply.assert_awaited_once_with(
        'Tier updated: <a href="https://t.me/alice">@alice</a> is now a resident.',
        disable_web_page_preview=True,
    )


async def test_user_tier_handle_must_belong_to_known_user() -> None:
    message = _message()
    user_service = SimpleNamespace(
        get_user_by_username=AsyncMock(return_value=None),
        is_bootstrap_resident=Mock(),
        set_tier=AsyncMock(),
    )

    await user_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args="member @missing"),
        user_service,
    )

    message.reply.assert_awaited_once_with(
        "User not found. They need to have interacted with Botka first."
    )
    user_service.set_tier.assert_not_awaited()
