from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botka.db.models import UserTier
from botka.handlers.visit.commands import visit_handler


def _user(
    user_id: int,
    telegram_id: int,
    username: str | None,
    tier: UserTier = UserTier.member,
):
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        username=username,
        tier=tier,
    )


def _message(*, send_message=None):
    bot = SimpleNamespace(
        send_message=send_message or AsyncMock(),
    )
    return SimpleNamespace(
        bot=bot,
        reply=AsyncMock(),
        reply_rich=AsyncMock(),
        answer=AsyncMock(),
    )


async def _call_handler(message, args, users, visits, actor) -> None:
    await visit_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args=args),
        users,
        visits,
        user_record=actor,
    )


@pytest.mark.parametrize("tier", [UserTier.resident, UserTier.member])
async def test_visit_without_arguments_sends_rich_help_for_allowed_tiers(
    tier: UserTier,
) -> None:
    message = _message()
    visits = SimpleNamespace(record_event=AsyncMock())

    await _call_handler(
        message,
        None,
        SimpleNamespace(),
        visits,
        _user(1, 10, None, tier),
    )

    visits.record_event.assert_awaited_once_with(1, "help")
    rich_message = message.reply_rich.await_args.args[0]
    assert rich_message.markdown.startswith("# Visit: manage visits to F0")
    assert "Syntax: `/visit <description>`\n" in rich_message.markdown
    assert "To stop tracking someone, type `/visit untrack" in rich_message.markdown
    assert "* `/visit untrack @alurm`" in rich_message.markdown
    assert "* `/visit untrack 322363419`" in rich_message.markdown


async def test_visit_rejects_guests_before_dispatch_or_event_logging() -> None:
    message = _message()
    visits = SimpleNamespace(record_event=AsyncMock())

    await _call_handler(
        message,
        "at 21:00",
        SimpleNamespace(),
        visits,
        _user(1, 10, "guest", UserTier.guest),
    )

    message.reply.assert_awaited_once_with("Only residents and members can use /visit.")
    message.reply_rich.assert_not_awaited()
    message.bot.send_message.assert_not_awaited()
    visits.record_event.assert_not_awaited()


async def test_visit_description_is_opaque_and_reports_partial_failure() -> None:
    planner = _user(1, 10, "planner")
    first = _user(2, 20, "first")
    second = _user(3, 30, "second")
    third = _user(4, 40, None)
    send = AsyncMock(side_effect=[None, None, RuntimeError("blocked")])
    message = _message(send_message=send)
    visits = SimpleNamespace(
        record_event=AsyncMock(),
        list_trackers=AsyncMock(return_value=[first, second, third]),
    )
    # `plan` has no special meaning and remains part of the description.
    description = "plan <b>literal</b> **also literal**"

    await _call_handler(
        message,
        description,
        SimpleNamespace(),
        visits,
        planner,
    )

    visits.record_event.assert_awaited_once_with(planner.id, "plan")
    assert send.await_count == 3
    assert send.await_args_list[0].kwargs["text"] == (
        '<a href="https://t.me/planner">@planner</a> plans to visit F0: '
        "plan &lt;b&gt;literal&lt;/b&gt; **also literal**"
    )
    report = message.reply.await_args.args[0]
    assert report == (
        "Visit announced to some trackers.\n"
        'Notified <a href="https://t.me/first">@first</a>, '
        '<a href="https://t.me/second">@second</a>.\n'
        'Couldn’t notify <a href="tg://user?id=40">40</a>.'
    )
    message.reply_rich.assert_not_awaited()


async def test_visit_plan_with_no_trackers_reports_that_nobody_was_notified() -> None:
    planner = _user(1, 10, "planner")
    message = _message()
    visits = SimpleNamespace(
        record_event=AsyncMock(),
        list_trackers=AsyncMock(return_value=[]),
    )

    await _call_handler(
        message,
        "later today",
        SimpleNamespace(),
        visits,
        planner,
    )

    message.bot.send_message.assert_not_awaited()
    message.reply.assert_awaited_once_with(
        "No one is tracking your visits, so no notifications were sent."
    )


async def test_visit_cancel_notifies_trackers_and_reports_success() -> None:
    planner = _user(1, 10, "planner")
    tracker = _user(2, 20, "tracker")
    message = _message()
    visits = SimpleNamespace(
        record_event=AsyncMock(),
        list_trackers=AsyncMock(return_value=[tracker]),
    )

    await _call_handler(
        message,
        "cancel",
        SimpleNamespace(),
        visits,
        planner,
    )

    visits.record_event.assert_awaited_once_with(planner.id, "cancel")
    notification = message.bot.send_message.await_args.kwargs["text"]
    assert notification == (
        '<a href="https://t.me/planner">@planner</a> canceled their visit.'
    )
    report = message.reply.await_args.args[0]
    assert report == (
        "Visit cancellation announced.\n"
        'Notified <a href="https://t.me/tracker">@tracker</a>.'
    )
    message.reply_rich.assert_not_awaited()


@pytest.mark.parametrize(
    ("target", "lookup"),
    [("@ALICE", "username"), ("322363419", "telegram_id")],
)
async def test_visit_track_resolves_handles_and_numeric_ids(target, lookup) -> None:
    actor = _user(1, 10, "actor")
    tracked = _user(2, 322363419, "alice" if lookup == "username" else None)
    message = _message()
    users = SimpleNamespace(
        get_user_by_username=AsyncMock(return_value=tracked),
        get_user=AsyncMock(return_value=tracked),
    )
    visits = SimpleNamespace(
        track=AsyncMock(return_value=True), record_event=AsyncMock()
    )

    await _call_handler(message, f"track {target}", users, visits, actor)

    visits.track.assert_awaited_once_with(actor.id, tracked.id)
    visits.record_event.assert_awaited_once_with(actor.id, "track")
    if lookup == "username":
        users.get_user_by_username.assert_awaited_once_with(target)
        users.get_user.assert_not_awaited()
    else:
        users.get_user.assert_awaited_once_with(322363419)
        users.get_user_by_username.assert_not_awaited()
    reply = message.reply.await_args
    assert reply is not None
    if tracked.username:
        assert reply.args[0] == 'You now track <a href="https://t.me/alice">@alice</a>.'
    else:
        assert reply.args[0] == (
            'You now track <a href="tg://user?id=322363419">322363419</a>.'
        )
    message.reply_rich.assert_not_awaited()


async def test_visit_tracking_and_trackers_render_current_relationships() -> None:
    actor = _user(1, 10, "actor")
    other = _user(2, 20, None)
    visits = SimpleNamespace(
        list_trackers=AsyncMock(return_value=[actor]),
        list_tracking=AsyncMock(return_value=[other]),
        record_event=AsyncMock(),
    )
    message = _message()

    await _call_handler(message, "trackers", SimpleNamespace(), visits, actor)
    await _call_handler(message, "tracking", SimpleNamespace(), visits, actor)

    visits.list_trackers.assert_awaited_once_with(actor.id)
    visits.list_tracking.assert_awaited_once_with(actor.id)
    assert message.reply.await_count == 2
    message.reply_rich.assert_not_awaited()
    assert visits.record_event.await_args_list[0].args == (actor.id, "trackers")
    assert visits.record_event.await_args_list[1].args == (actor.id, "tracking")
    assert message.reply.await_args_list[0].args[0] == (
        'Your visits are tracked by:\n• <a href="https://t.me/actor">@actor</a>'
    )
    assert message.reply.await_args_list[1].args[0] == (
        'You track visits of:\n• <a href="tg://user?id=20">20</a>'
    )
