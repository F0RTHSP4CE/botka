from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings


@dataclass(frozen=True)
class PeriodicContext:
    bot: Bot
    settings: Settings
    sessionmaker: async_sessionmaker


@dataclass(frozen=True)
class PeriodicJob:
    name: str
    handler: Callable[[PeriodicContext], Awaitable[None]]
    interval_seconds: int | None = None
    cron_hour: int | None = None
    cron_minute: int | None = None
