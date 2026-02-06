from __future__ import annotations

import asyncio
import logging
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings
from botka.periodic.jobs import PeriodicContext, PeriodicJob
from botka.periodic.schedule import build_schedule, get_job

logger = logging.getLogger(__name__)


async def run_periodic_job(
    bot: Bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
    job_name: str,
) -> bool:
    job = get_job(settings, job_name)
    if job is None:
        return False
    context = PeriodicContext(bot=bot, settings=settings, sessionmaker=sessionmaker)
    await _run_job(job, context)
    return True


async def periodic_loop(
    bot: Bot,
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    jobs = list(build_schedule(settings))
    if not jobs:
        return
    context = PeriodicContext(bot=bot, settings=settings, sessionmaker=sessionmaker)
    timezone = _resolve_timezone(settings)
    scheduler = AsyncIOScheduler(timezone=timezone)
    for job in jobs:
        trigger = _build_trigger(job, timezone)
        if trigger is None:
            logger.warning("Skipping periodic job %s without a schedule", job.name)
            continue
        scheduler.add_job(
            _run_job,
            trigger,
            args=[job, context],
            id=job.name,
            replace_existing=True,
        )
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


async def _run_job(job: PeriodicJob, context: PeriodicContext) -> None:
    try:
        await job.handler(context)
    except Exception:
        logger.exception("Periodic job %s failed", job.name)


def _build_trigger(job: PeriodicJob, timezone=None):
    if job.interval_seconds is not None:
        interval = max(job.interval_seconds, 1.0)
        return IntervalTrigger(seconds=interval)
    if job.cron_hour is not None or job.cron_minute is not None:
        return CronTrigger(
            hour=job.cron_hour,
            minute=job.cron_minute,
            timezone=timezone,
        )
    return None


def _resolve_timezone(settings: Settings):
    timezone_name = settings.timezone or os.environ.get("TZ")
    if not timezone_name:
        return None
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone %s, using local timezone", timezone_name)
        return None
