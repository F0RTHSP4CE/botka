from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def call_with_retry_after(
    operation: Callable[[], Awaitable[T]],
    *,
    description: str,
) -> T:
    while True:
        try:
            return await operation()
        except TelegramRetryAfter as exc:
            logger.warning(
                "%s rate-limited; retrying in %ss",
                description,
                exc.retry_after,
            )
            await asyncio.sleep(exc.retry_after)
