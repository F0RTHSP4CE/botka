from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from botka.services.mac_tracker_service import _notify_visit_arrivals


async def test_visit_arrivals_notify_all_trackers_despite_delivery_failure() -> None:
    arrived = SimpleNamespace(id=1, telegram_id=10, username="arrived")
    first = SimpleNamespace(id=2, telegram_id=20, username="first")
    second = SimpleNamespace(id=3, telegram_id=30, username=None)
    visits = SimpleNamespace(
        list_trackers_for_users=AsyncMock(return_value={1: [first, second]})
    )
    bot = SimpleNamespace(
        send_rich_message=AsyncMock(side_effect=[RuntimeError("blocked"), None])
    )

    await _notify_visit_arrivals(bot, visits, {1}, {1: arrived})

    visits.list_trackers_for_users.assert_awaited_once_with({1})
    assert bot.send_rich_message.await_count == 2
    notification = bot.send_rich_message.await_args_list[1].kwargs["rich_message"]
    assert notification.blocks[0].text[1] == " has arrived at F0."
