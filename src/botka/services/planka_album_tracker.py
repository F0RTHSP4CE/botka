from __future__ import annotations

import asyncio


class PlankaAlbumTracker:
    """Tracks pending card-creation futures for Telegram album messages.

    When a /todo command arrives with a media_group_id, a Future is registered
    here so that subsequent photos in the same album can upload themselves to
    the newly-created Planka card as soon as its ID becomes available.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[str]] = {}

    def create_pending(self, media_group_id: str) -> asyncio.Future[str]:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[media_group_id] = future
        return future

    def set_result(self, media_group_id: str, card_id: str) -> None:
        future = self._pending.get(media_group_id)
        if future is not None and not future.done():
            future.set_result(card_id)

    def get(self, media_group_id: str) -> asyncio.Future[str] | None:
        return self._pending.get(media_group_id)

    def discard(self, media_group_id: str) -> None:
        self._pending.pop(media_group_id, None)

    def has(self, media_group_id: str) -> bool:
        return media_group_id in self._pending
