from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from botka.db.models import PollAudience
from botka.periodic.jobs.polls import _post_poll_decision
from botka.services.polls_service import PollsService


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent_messages.append(kwargs)


@pytest.mark.asyncio
async def test_post_poll_decision_uses_poll_result_vote_totals(session):
    service = PollsService(session)
    poll = await service.create_poll(
        poll_id="poll-1",
        chat_id=-1001234567890,
        message_id=777,
        author_telegram_id=1001,
        question="Should we buy it?",
        audience=PollAudience.residents,
        awaiting_message_id=123,
        closes_at=datetime.now(timezone.utc),
    )
    await service.set_poll_options(poll.poll_id, ["yes", "see results"])
    await service.set_ignored_option_ids(poll.poll_id, [1])

    for user_id in (1001, 1002, 1003):
        await service.set_option_votes(poll.poll_id, user_id, [0])

    poll_result = SimpleNamespace(
        options=[
            SimpleNamespace(text="yes", voter_count=9),
            SimpleNamespace(text="see results", voter_count=3),
        ]
    )
    bot = _FakeBot()
    context = SimpleNamespace(
        bot=bot,
        settings=SimpleNamespace(decisions_chat_id=-100222, decisions_topic_id=333),
    )

    await _post_poll_decision(context, service, poll, poll_result)

    assert len(bot.sent_messages) == 1
    assert "Result: <b>yes</b> (9 votes)" in bot.sent_messages[0]["text"]
