from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from botka.db.models import PollAudience
from botka.handlers.polls.commands import create_poll_from_command


async def test_poll_command_creates_public_poll_with_default_options(settings):
    sent_poll = SimpleNamespace(
        poll=SimpleNamespace(id="poll-1"),
        chat=SimpleNamespace(id=-100),
        message_id=20,
    )
    bot = SimpleNamespace(
        send_poll=AsyncMock(return_value=sent_poll),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=21)),
        pin_chat_message=AsyncMock(),
    )
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-100),
        message_thread_id=42,
        reply_to_message=None,
        from_user=SimpleNamespace(id=123, username="author"),
        delete=AsyncMock(),
        reply=AsyncMock(),
    )
    polls_service = SimpleNamespace(
        list_target_users=AsyncMock(return_value=[]),
        create_poll=AsyncMock(),
        set_ignored_option_ids=AsyncMock(),
        set_poll_options=AsyncMock(),
    )

    await create_poll_from_command(
        message,
        SimpleNamespace(args="[members] Accept @RedTeapot as a member?"),
        polls_service,
        settings,
    )

    send_poll_kwargs = bot.send_poll.await_args.kwargs
    assert send_poll_kwargs["question"] == (
        "[members] Accept @RedTeapot as a member?"
    )
    assert [option.text for option in send_poll_kwargs["options"]] == [
        "Yes",
        "No",
        "See results",
    ]
    assert send_poll_kwargs["is_anonymous"] is False
    assert send_poll_kwargs["allows_multiple_answers"] is False
    polls_service.create_poll.assert_awaited_once()
    assert (
        polls_service.create_poll.await_args.kwargs["audience"]
        is PollAudience.members
    )
    polls_service.set_ignored_option_ids.assert_awaited_once_with("poll-1", {2})
    polls_service.set_poll_options.assert_awaited_once_with(
        "poll-1", ["Yes", "No", "See results"]
    )
    bot.pin_chat_message.assert_awaited_once()
    message.delete.assert_awaited_once()


async def test_poll_command_defaults_to_residents(settings):
    bot = SimpleNamespace(
        send_poll=AsyncMock(
            return_value=SimpleNamespace(
                poll=SimpleNamespace(id="poll-2"),
                chat=SimpleNamespace(id=-100),
                message_id=30,
            )
        ),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=31)),
        pin_chat_message=AsyncMock(),
    )
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=-100),
        message_thread_id=None,
        reply_to_message=None,
        from_user=SimpleNamespace(id=123, username=None),
        delete=AsyncMock(),
        reply=AsyncMock(),
    )
    polls_service = SimpleNamespace(
        list_target_users=AsyncMock(return_value=[]),
        create_poll=AsyncMock(),
        set_ignored_option_ids=AsyncMock(),
        set_poll_options=AsyncMock(),
    )

    await create_poll_from_command(
        message,
        SimpleNamespace(args="Should we buy a new kettle?"),
        polls_service,
        settings,
    )

    assert bot.send_poll.await_args.kwargs["question"] == (
        "[residents] Should we buy a new kettle?"
    )
    assert polls_service.create_poll.await_args.kwargs["audience"] is (
        PollAudience.residents
    )


async def test_poll_command_without_question_shows_usage(settings):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        reply=AsyncMock(),
    )
    polls_service = SimpleNamespace()

    await create_poll_from_command(
        message,
        SimpleNamespace(args=None),
        polls_service,
        settings,
    )

    message.reply.assert_awaited_once_with(
        "Usage: /poll [residents|members|everyone] &lt;question&gt;"
    )


async def test_poll_command_rejects_question_over_telegram_limit(settings):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        reply=AsyncMock(),
    )

    await create_poll_from_command(
        message,
        SimpleNamespace(args="x" * 300),
        SimpleNamespace(),
        settings,
    )

    message.reply.assert_awaited_once_with(
        "Poll question is too long (maximum 300 characters, including the audience tag)."
    )
