from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from botka.config import Settings
from botka.db.session import init_models
from botka.di.container import build_container
from botka.handlers import (
    borrowed,
    doors,
    help,
    mac_tracker,
    periodic,
    pins,
    shopping,
    users,
)
from botka.handlers.pins.messages import NewTopicForwardMiddleware
from botka.handlers.polls import answers as poll_answers
from botka.handlers.polls import commands as poll_commands
from botka.handlers.polls import messages as poll_messages
from botka.mac_tracker.web import run_mac_tracker_server
from botka.middlewares import UserSyncMiddleware
from botka.periodic import periodic_loop
from botka.services.mac_tracker_service import mac_tracker_poll_loop


async def _run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if not settings.mac_tracker_jwt_secret:
        settings.mac_tracker_jwt_secret = secrets.token_urlsafe(48)
    container = build_container(settings)

    engine = await container.get(AsyncEngine)
    await init_models(engine)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(help.commands.router)
    dp.include_router(users.commands.router)
    dp.include_router(doors.commands.router)
    dp.include_router(mac_tracker.commands.router)
    dp.include_router(mac_tracker.callbacks.router)
    dp.include_router(borrowed.commands.router)
    dp.include_router(shopping.commands.router)
    dp.include_router(poll_commands.router)
    dp.include_router(shopping.messages.router)
    dp.include_router(shopping.callbacks.router)
    dp.include_router(borrowed.messages.router)
    dp.include_router(doors.callbacks.router)
    dp.include_router(borrowed.callbacks.router)
    dp.include_router(pins.messages.router)
    dp.include_router(poll_messages.router)
    dp.include_router(poll_answers.router)
    dp.include_router(periodic.commands.router)

    setup_dishka(container, dp)

    sessionmaker = await container.get(async_sessionmaker)
    user_sync = UserSyncMiddleware(sessionmaker, settings)
    dp.message.middleware(user_sync)
    dp.message.middleware(NewTopicForwardMiddleware(settings))
    dp.callback_query.middleware(user_sync)
    dp.poll_answer.middleware(user_sync)
    mac_poll_task = asyncio.create_task(
        mac_tracker_poll_loop(bot, sessionmaker, settings)
    )
    mac_web_task = asyncio.create_task(run_mac_tracker_server(settings, sessionmaker))
    periodic_task = asyncio.create_task(periodic_loop(bot, sessionmaker, settings))
    try:
        await dp.start_polling(bot)
    finally:
        mac_poll_task.cancel()
        mac_web_task.cancel()
        periodic_task.cancel()
        await asyncio.gather(
            mac_poll_task,
            mac_web_task,
            periodic_task,
            return_exceptions=True,
        )
        await container.close()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())
