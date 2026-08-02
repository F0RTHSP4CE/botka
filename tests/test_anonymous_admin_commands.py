from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import PromoteChatMember

from botka.db.models import UserTier
from botka.handlers.anonymous_admins.commands import (
    ADMIN_PERMISSION_FIELDS,
    anon_handler,
    deanon_handler,
)


def _message(*, event_log: list[str] | None = None) -> SimpleNamespace:
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(),
        promote_chat_member=AsyncMock(),
    )

    async def delete() -> None:
        if event_log is not None:
            event_log.append("delete-command")

    async def answer(_text: str) -> None:
        if event_log is not None:
            event_log.append("answer")

    return SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-100123, type=ChatType.SUPERGROUP),
        sender_chat=None,
        reply=AsyncMock(),
        answer=AsyncMock(side_effect=answer),
        delete=AsyncMock(side_effect=delete),
    )


def _resident() -> SimpleNamespace:
    return SimpleNamespace(tier=UserTier.resident)


def _settings(*group_ids: int) -> SimpleNamespace:
    return SimpleNamespace(
        allowed_anon_group_ids=list(group_ids) if group_ids else [-100123]
    )


def _admin_member(telegram_id: int = 2) -> SimpleNamespace:
    values = {
        field: field in {"can_manage_chat", "can_delete_messages"}
        for field in ADMIN_PERMISSION_FIELDS
    }
    values["is_anonymous"] = False
    return SimpleNamespace(
        status=ChatMemberStatus.ADMINISTRATOR,
        user=SimpleNamespace(id=telegram_id),
        can_be_edited=True,
        **values,
    )


@pytest.mark.asyncio
async def test_anon_promotes_chat_residents_and_snapshots_prior_state() -> None:
    message = _message()
    member = SimpleNamespace(status=ChatMemberStatus.MEMBER)
    admin = _admin_member()
    owner = SimpleNamespace(status=ChatMemberStatus.CREATOR)
    left = SimpleNamespace(status=ChatMemberStatus.LEFT)
    message.bot.get_chat_member.side_effect = [member, admin, owner, left]

    snapshots = []

    async def save_snapshot(chat_id, telegram_id, **kwargs):
        snapshot = SimpleNamespace(
            chat_id=chat_id,
            telegram_id=telegram_id,
            **kwargs,
        )
        snapshots.append(snapshot)
        return snapshot

    service = SimpleNamespace(
        list_resident_ids=AsyncMock(return_value=[1, 2, 3, 4]),
        get_snapshot=AsyncMock(return_value=None),
        save_snapshot=AsyncMock(side_effect=save_snapshot),
        delete_snapshot=AsyncMock(),
    )

    await anon_handler.__dishka_orig_func__(
        message, service, _settings(), _resident()
    )

    assert [snapshot.telegram_id for snapshot in snapshots] == [1, 2]
    assert snapshots[0].was_administrator is False
    assert snapshots[0].permissions == {}
    assert snapshots[1].was_administrator is True
    assert snapshots[1].permissions["can_delete_messages"] is True
    assert snapshots[1].permissions["is_anonymous"] is False
    assert message.bot.promote_chat_member.await_args_list == [
        call(-100123, 1, is_anonymous=True, can_manage_chat=True),
        call(-100123, 2, is_anonymous=True, can_manage_chat=True),
    ]
    message.delete.assert_awaited_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_anon_rejects_non_resident() -> None:
    message = _message()
    service = SimpleNamespace(list_resident_ids=AsyncMock())

    await anon_handler.__dishka_orig_func__(
        message,
        service,
        _settings(),
        SimpleNamespace(tier=UserTier.member),
    )

    message.delete.assert_awaited_once()
    message.reply.assert_not_awaited()
    service.list_resident_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_anon_rejects_group_outside_allowlist() -> None:
    message = _message()
    service = SimpleNamespace(list_resident_ids=AsyncMock())

    await anon_handler.__dishka_orig_func__(
        message, service, _settings(-100999), _resident()
    )

    message.delete.assert_awaited_once()
    service.list_resident_ids.assert_not_awaited()
    message.bot.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_deletes_command_first_and_restores_snapshots() -> None:
    events: list[str] = []
    message = _message(event_log=events)

    async def promote(*_args, **_kwargs) -> None:
        events.append("restore")

    message.bot.promote_chat_member.side_effect = promote
    member_snapshot = SimpleNamespace(
        telegram_id=1,
        was_administrator=False,
        permissions={},
    )
    admin_permissions = {
        field: field in {"can_manage_chat", "can_pin_messages"}
        for field in ADMIN_PERMISSION_FIELDS
    }
    admin_permissions["is_anonymous"] = False
    admin_snapshot = SimpleNamespace(
        telegram_id=2,
        was_administrator=True,
        permissions=admin_permissions,
    )
    service = SimpleNamespace(
        list_snapshots=AsyncMock(
            side_effect=lambda _chat_id: events.append("load")
            or [member_snapshot, admin_snapshot]
        ),
        delete_snapshot=AsyncMock(),
    )

    await deanon_handler.__dishka_orig_func__(
        message, service, _settings(), _resident()
    )

    assert events == ["delete-command", "load", "restore", "restore"]
    first_restore = message.bot.promote_chat_member.await_args_list[0]
    assert first_restore.args == (-100123, 1)
    assert all(value is False for value in first_restore.kwargs.values())
    message.bot.promote_chat_member.assert_any_await(
        -100123,
        2,
        **admin_permissions,
    )
    assert service.delete_snapshot.await_args_list == [
        call(member_snapshot),
        call(admin_snapshot),
    ]
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_deletes_command_before_rejecting_non_resident() -> None:
    events: list[str] = []
    message = _message(event_log=events)
    service = SimpleNamespace(list_snapshots=AsyncMock())

    await deanon_handler.__dishka_orig_func__(
        message,
        service,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    assert events == ["delete-command"]
    service.list_snapshots.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_accepts_anonymous_admin_of_same_chat() -> None:
    events: list[str] = []
    message = _message(event_log=events)
    message.sender_chat = SimpleNamespace(id=message.chat.id)
    service = SimpleNamespace(
        list_snapshots=AsyncMock(return_value=[]),
        delete_snapshot=AsyncMock(),
    )

    await deanon_handler.__dishka_orig_func__(
        message,
        service,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    assert events == ["delete-command"]
    service.list_snapshots.assert_awaited_once_with(message.chat.id)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_rejects_message_sent_as_another_chat() -> None:
    message = _message()
    message.sender_chat = SimpleNamespace(id=-100999)
    service = SimpleNamespace(list_snapshots=AsyncMock())

    await deanon_handler.__dishka_orig_func__(
        message,
        service,
        _settings(),
        SimpleNamespace(tier=UserTier.guest),
    )

    service.list_snapshots.assert_not_awaited()
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_rejects_group_outside_allowlist_after_deleting_command() -> None:
    events: list[str] = []
    message = _message(event_log=events)
    message.sender_chat = SimpleNamespace(id=message.chat.id)
    service = SimpleNamespace(list_snapshots=AsyncMock())

    await deanon_handler.__dishka_orig_func__(
        message,
        service,
        _settings(-100999),
        SimpleNamespace(tier=UserTier.guest),
    )

    assert events == ["delete-command"]
    service.list_snapshots.assert_not_awaited()
    message.bot.promote_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_deanon_keeps_snapshot_when_restore_fails() -> None:
    message = _message()
    snapshot = SimpleNamespace(
        telegram_id=1,
        was_administrator=False,
        permissions={},
    )
    service = SimpleNamespace(
        list_snapshots=AsyncMock(return_value=[snapshot]),
        delete_snapshot=AsyncMock(),
    )
    message.bot.promote_chat_member.side_effect = TelegramBadRequest(
        method=PromoteChatMember(chat_id=-100123, user_id=1),
        message="not enough rights",
    )

    await deanon_handler.__dishka_orig_func__(
        message, service, _settings(), _resident()
    )

    service.delete_snapshot.assert_not_awaited()
    message.answer.assert_not_awaited()
