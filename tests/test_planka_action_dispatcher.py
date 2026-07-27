import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.services.planka_action_dispatcher import (
    LocalActionNotification,
    PlankaActionDispatcher,
)


@pytest.mark.asyncio
async def test_dispatch_runs_notification_and_refresh_in_background(
    engine, settings
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    started_count = 0

    async def _block(*args, **kwargs) -> None:
        nonlocal started_count
        started_count += 1
        if started_count == 2:
            started.set()
        await release.wait()

    notifications = SimpleNamespace(notify_local_action=AsyncMock(side_effect=_block))
    publisher = SimpleNamespace(refresh_safely=AsyncMock(side_effect=_block))
    dispatcher = PlankaActionDispatcher(
        async_sessionmaker(engine, expire_on_commit=False),
        settings,
        SimpleNamespace(),
        SimpleNamespace(),
        notifications,
        publisher,
        refresh_debounce_seconds=0,
    )
    bot = SimpleNamespace()

    dispatcher.dispatch(
        bot,
        LocalActionNotification("✅ Complete", "moveCard", "card-1"),
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    tasks = tuple(dispatcher._tasks)
    assert tasks
    release.set()
    await asyncio.gather(*tasks)
    await dispatcher.close()

    notifications.notify_local_action.assert_awaited_once_with(
        bot,
        "✅ Complete",
        action_type="moveCard",
        card_id="card-1",
    )
    publisher.refresh_safely.assert_awaited_once()


@pytest.mark.asyncio
async def test_burst_refreshes_are_coalesced(engine, settings) -> None:
    publisher = SimpleNamespace(refresh_safely=AsyncMock())
    dispatcher = PlankaActionDispatcher(
        async_sessionmaker(engine, expire_on_commit=False),
        settings,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        publisher,
        refresh_debounce_seconds=0,
    )

    dispatcher.dispatch(SimpleNamespace())
    dispatcher.dispatch(SimpleNamespace())
    await asyncio.gather(*tuple(dispatcher._tasks))

    publisher.refresh_safely.assert_awaited_once()


@pytest.mark.asyncio
async def test_action_during_refresh_triggers_one_latest_follow_up(
    engine, settings
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def _refresh(*args) -> None:
        if not started.is_set():
            started.set()
            await release.wait()

    publisher = SimpleNamespace(refresh_safely=AsyncMock(side_effect=_refresh))
    dispatcher = PlankaActionDispatcher(
        async_sessionmaker(engine, expire_on_commit=False),
        settings,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        publisher,
        refresh_debounce_seconds=0,
    )

    dispatcher.dispatch(SimpleNamespace())
    await asyncio.wait_for(started.wait(), timeout=1)
    dispatcher.dispatch(SimpleNamespace())
    tasks = tuple(dispatcher._tasks)
    release.set()
    await asyncio.gather(*tasks)

    assert publisher.refresh_safely.await_count == 2
