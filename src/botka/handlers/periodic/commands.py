from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.periodic.runner import run_periodic_job
from botka.periodic.schedule import build_schedule

router = Router(name=__name__)


async def _do_periodic(message: Message, settings: Settings) -> None:
    jobs = list(build_schedule(settings))
    if not jobs:
        await message.reply("No periodic jobs configured.")
        return
    lines = []
    for job in jobs:
        interval = _format_job_schedule(job)
        lines.append(f"- {job.name} ({interval})")
    await message.reply(
        "Periodic jobs:\n" + "\n".join(lines),
        disable_web_page_preview=True,
    )


@router.message(Command("periodic"))
@inject
async def periodic_list_handler(
    message: Message,
    settings: FromDishka[Settings],
) -> None:
    await _do_periodic(message, settings)


@router.message(Command("periodic_run"))
@inject
async def periodic_run_handler(
    message: Message,
    command: CommandObject,
    settings: FromDishka[Settings],
    sessionmaker: FromDishka[async_sessionmaker],
    user_record: User | None = None,
) -> None:
    if message.from_user is None:
        await message.reply("Unknown user.")
        return
    tier = user_record.tier if user_record else UserTier.guest
    if tier != UserTier.resident:
        await message.reply("Only residents can run periodic jobs.")
        return
    job_name = (command.args or "").strip()
    if not job_name:
        await message.reply("Usage: /periodic_run <job_name>")
        return
    triggered = await run_periodic_job(
        message.bot,
        sessionmaker,
        settings,
        job_name,
    )
    if not triggered:
        await message.reply("Unknown job. Use /periodic to list available jobs.")
        return
    await message.reply(f"Triggered periodic job: {html.escape(job_name)}")


def _format_job_schedule(job) -> str:
    if job.interval_seconds is not None:
        seconds = job.interval_seconds
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = round(seconds / 60)
        if minutes < 60:
            return f"{minutes}m"
        hours = round(minutes / 60)
        return f"{hours}h"
    if job.cron_hour is not None or job.cron_minute is not None:
        hour = job.cron_hour if job.cron_hour is not None else "*"
        minute = job.cron_minute if job.cron_minute is not None else "*"
        return f"cron {hour}:{minute}"
    return "unscheduled"

    if job.interval_seconds is not None:
        seconds = job.interval_seconds
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = round(seconds / 60)
        if minutes < 60:
            return f"{minutes}m"
        hours = round(minutes / 60)
        return f"{hours}h"
    if job.cron_hour is not None or job.cron_minute is not None:
        hour = job.cron_hour if job.cron_hour is not None else "*"
        minute = job.cron_minute if job.cron_minute is not None else "*"
        return f"cron {hour}:{minute}"
    return "unscheduled"
