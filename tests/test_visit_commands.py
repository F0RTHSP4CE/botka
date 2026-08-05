from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botka.handlers.visit.commands import visit_handler


def _user(user_id: int, telegram_id: int, username: str | None):
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        username=username,
    )


def _message(*, send_rich_message=None):
    bot = SimpleNamespace(
        send_rich_message=send_rich_message or AsyncMock(),
    )
    return SimpleNamespace(
        bot=bot,
        reply=AsyncMock(),
        reply_rich=AsyncMock(),
        answer_rich=AsyncMock(),
    )


async def _call_handler(message, args, users, visits, actor) -> None:
    await visit_handler.__dishka_orig_func__(
        message,
        SimpleNamespace(args=args),
        users,
        visits,
        user_record=actor,
    )


async def test_visit_without_arguments_sends_rich_help() -> None:
    message = _message()
    visits = SimpleNamespace(record_event=AsyncMock())

    await _call_handler(message, None, SimpleNamespace(), visits, _user(1, 10, None))

    visits.record_event.assert_awaited_once_with(1, "help")
    rich_message = message.reply_rich.await_args.args[0]
    assert rich_message.markdown.startswith("# Visit: manage visits to F0")


async def test_visit_plan_preserves_plain_description_and_reports_partial_failure() -> (
    None
):
    planner = _user(1, 10, "planner")
    first = _user(2, 20, "first")
    second = _user(3, 30, None)
    send = AsyncMock(side_effect=[None, RuntimeError("blocked")])
    message = _message(send_rich_message=send)
    visits = SimpleNamespace(
        record_event=AsyncMock(),
        list_trackers=AsyncMock(return_value=[first, second]),
    )
    description = "<b>literal</b> **also literal**"

    await _call_handler(
        message,
        f"plan {description}",
        SimpleNamespace(),
        visits,
        planner,
    )

    visits.record_event.assert_awaited_once_with(planner.id, "plan")
    assert send.await_count == 2
    notification = send.await_args_list[0].kwargs["rich_message"]
    assert notification.blocks[1].text == description
    report = message.reply_rich.await_args.args[0]
    assert report.blocks[0].text == "Visit announced to some trackers."


async def test_visit_plan_with_no_trackers_reports_that_nobody_was_notified() -> None:
    planner = _user(1, 10, "planner")
    message = _message()
    visits = SimpleNamespace(
        record_event=AsyncMock(),
        list_trackers=AsyncMock(return_value=[]),
    )

    await _call_handler(
        message,
        "plan later today",
        SimpleNamespace(),
        visits,
        planner,
    )

    message.bot.send_rich_message.assert_not_awaited()
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
    notification = message.bot.send_rich_message.await_args.kwargs["rich_message"]
    assert notification.blocks[0].text[1] == " canceled their visit."
    report = message.reply_rich.await_args.args[0]
    assert report.blocks[0].text == "Visit cancellation announced."


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
    assert message.reply_rich.await_count == 2
    assert visits.record_event.await_args_list[0].args == (actor.id, "trackers")
    assert visits.record_event.await_args_list[1].args == (actor.id, "tracking")
