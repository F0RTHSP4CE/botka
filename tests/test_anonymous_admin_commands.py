from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import PromoteChatMember

from botka.db.models import UserTier
from botka.handlers.anonymous_admins import commands
from botka.handlers.anonymous_admins.commands import (
    ADMIN_PERMISSION_FIELDS,
    anon_handler,
    deanon_handler,
)


@pytest.fixture(autouse=True)
def disable_admin_operation_cooldown(monkeypatch) -> AsyncMock:
    commands._command_last_run_at.clear()
    sleep = AsyncMock()
    monkeypatch.setattr(commands, "cooldown_sleep", sleep)
    return sleep


def _message(*, event_log: list[str] | None = None) -> SimpleNamespace:
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(),
        get_chat_administrators=AsyncMock(return_value=[]),
        promote_chat_member=AsyncMock(),
    )

    async def delete() -> None:
        if event_log is not None:
            event_log.append("delete-command")

    return SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-100123, type=ChatType.SUPERGROUP),
        sender_chat=None,
        reply=AsyncMock(),
        answer=AsyncMock(),
        delete=AsyncMock(side_effect=delete),
    )


def _resident() -> SimpleNamespace:
    return SimpleNamespace(tier=UserTier.resident)


def _member() -> SimpleNamespace:
    return SimpleNamespace(tier=UserTier.member)


def _settings(*group_ids: int) -> SimpleNamespace:
    return SimpleNamespace(
        allowed_anon_group_ids=list(group_ids) if group_ids else [-100123]
    )


def _administrator(
    telegram_id: int,
    *,
    anonymous: bool,
    editable: bool = True,
    status: ChatMemberStatus = ChatMemberStatus.ADMINISTRATOR,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        user=SimpleNamespace(id=telegram_id),
        is_anonymous=anonymous,
        can_be_edited=editable,
    )


@pytest.mark.asyncio
async def test_anon_promotes_eligible_residents_without_snapshots() -> None:
    message = _message()
    message.bot.get_chat_member.side_effect = [
        SimpleNamespace(status=ChatMemberStatus.MEMBER),
        SimpleNamespace(
            status=ChatMemberStatus.ADMINISTRATOR,
            can_be_edited=True,
        ),
        SimpleNamespace(status=ChatMemberStatus.CREATOR),
        SimpleNamespace(status=ChatMemberStatus.LEFT),
    ]
    user_service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[1, 2, 3, 4])
    )

    await anon_handler.__dishka_orig_func__(
        message, user_service, _settings(), _resident()
    )

    assert message.bot.promote_chat_member.await_args_list == [
        call(-100123, 1, is_anonymous=True, can_manage_chat=True),
        call(-100123, 2, is_anonymous=True, can_manage_chat=True),
    ]
    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_anon_honors_promote_chat_member_retry_after(monkeypatch) -> None:
    message = _message()
    message.bot.get_chat_member.return_value = SimpleNamespace(
        status=ChatMemberStatus.MEMBER
    )
    retry_after = TelegramRetryAfter(
        method=PromoteChatMember(chat_id=-100123, user_id=1),
        message="Too Many Requests",
        retry_after=30,
    )
    message.bot.promote_chat_member.side_effect = [retry_after, True]
    user_service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[1])
    )
    retry_sleep = AsyncMock()
    monkeypatch.setattr(
        "botka.services.telegram_retry.asyncio.sleep",
        retry_sleep,
    )

    await anon_handler.__dishka_orig_func__(
        message, user_service, _settings(), _resident()
    )

    assert message.bot.promote_chat_member.await_count == 2
    retry_sleep.assert_awaited_once_with(30)


@pytest.mark.asyncio
async def test_anon_rejects_guest() -> None:
    message = _message()
    user_service = SimpleNamespace(list_resident_ids=AsyncMock())

    await anon_handler.__dishka_orig_func__(
        message,
        user_service,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    message.delete.assert_awaited_once()
    user_service.list_resident_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_anon_accepts_member_tier() -> None:
    message = _message()
    user_service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[])
    )

    await anon_handler.__dishka_orig_func__(
        message, user_service, _settings(), _member()
    )

    user_service.list_resident_ids.assert_awaited_once()


@pytest.mark.asyncio
async def test_anon_rejects_group_outside_allowlist() -> None:
    message = _message()
    user_service = SimpleNamespace(list_resident_ids=AsyncMock())

    await anon_handler.__dishka_orig_func__(
        message, user_service, _settings(-100999), _resident()
    )

    user_service.list_resident_ids.assert_not_awaited()
    message.bot.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_anon_has_five_minute_per_chat_cooldown(monkeypatch) -> None:
    message = _message()
    user_service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        commands,
        "monotonic",
        Mock(side_effect=[100.0, 399.9, 400.0]),
    )

    for _ in range(3):
        await anon_handler.__dishka_orig_func__(
            message, user_service, _settings(), _member()
        )

    assert user_service.list_resident_ids.await_count == 2
    assert message.delete.await_count == 3


@pytest.mark.asyncio
async def test_deanon_dismisses_all_editable_anonymous_administrators() -> None:
    events: list[str] = []
    message = _message(event_log=events)
    administrators = [
        _administrator(1, anonymous=True),
        _administrator(2, anonymous=False),
        _administrator(3, anonymous=True, editable=False),
        _administrator(
            4,
            anonymous=True,
            editable=False,
            status=ChatMemberStatus.CREATOR,
        ),
    ]

    async def get_administrators(_chat_id):
        events.append("list-administrators")
        return administrators

    async def promote(*_args, **_kwargs):
        events.append("dismiss")

    message.bot.get_chat_administrators.side_effect = get_administrators
    message.bot.promote_chat_member.side_effect = promote

    await deanon_handler.__dishka_orig_func__(
        message, _settings(), _resident()
    )

    assert events == ["delete-command", "list-administrators", "dismiss"]
    dismissal = message.bot.promote_chat_member.await_args
    assert dismissal.args == (-100123, 1)
    assert all(value is False for value in dismissal.kwargs.values())


@pytest.mark.asyncio
async def test_deanon_deletes_command_before_rejecting_guest() -> None:
    events: list[str] = []
    message = _message(event_log=events)

    await deanon_handler.__dishka_orig_func__(
        message,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    assert events == ["delete-command"]
    message.bot.get_chat_administrators.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_accepts_anonymous_admin_of_same_chat() -> None:
    message = _message()
    message.sender_chat = SimpleNamespace(id=message.chat.id)

    await deanon_handler.__dishka_orig_func__(
        message,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    message.bot.get_chat_administrators.assert_awaited_once_with(message.chat.id)


@pytest.mark.asyncio
async def test_deanon_accepts_visible_member_tier() -> None:
    message = _message()

    await deanon_handler.__dishka_orig_func__(message, _settings(), _member())

    message.bot.get_chat_administrators.assert_awaited_once_with(message.chat.id)


@pytest.mark.asyncio
async def test_anon_and_deanon_share_five_minute_cooldown(monkeypatch) -> None:
    message = _message()
    user_service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        commands,
        "monotonic",
        Mock(side_effect=[100.0, 399.9, 400.0]),
    )

    await anon_handler.__dishka_orig_func__(
        message, user_service, _settings(), _resident()
    )
    await deanon_handler.__dishka_orig_func__(message, _settings(), _resident())
    await deanon_handler.__dishka_orig_func__(message, _settings(), _resident())

    user_service.list_resident_ids.assert_awaited_once()
    message.bot.get_chat_administrators.assert_awaited_once_with(message.chat.id)


@pytest.mark.asyncio
async def test_deanon_rejects_message_sent_as_another_chat() -> None:
    message = _message()
    message.sender_chat = SimpleNamespace(id=-100999)

    await deanon_handler.__dishka_orig_func__(
        message,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    message.bot.get_chat_administrators.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_rejects_group_outside_allowlist() -> None:
    message = _message()
    message.sender_chat = SimpleNamespace(id=message.chat.id)

    await deanon_handler.__dishka_orig_func__(
        message,
        _settings(-100999),
        SimpleNamespace(tier=UserTier.guest),
    )

    message.bot.get_chat_administrators.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_continues_after_one_dismissal_fails() -> None:
    message = _message()
    message.bot.get_chat_administrators.return_value = [
        _administrator(1, anonymous=True),
        _administrator(2, anonymous=True),
    ]
    message.bot.promote_chat_member.side_effect = [
        TelegramBadRequest(
            method=PromoteChatMember(chat_id=-100123, user_id=1),
            message="not enough rights",
        ),
        True,
    ]

    await deanon_handler.__dishka_orig_func__(
        message, _settings(), _resident()
    )

    assert message.bot.promote_chat_member.await_count == 2
