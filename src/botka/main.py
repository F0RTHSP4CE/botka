from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from botka.config import Settings
from botka.db.session import init_models
from botka.di.container import build_container
from botka.handlers import borrowed, doors, help, shopping, users
from botka.handlers.polls import answers as poll_answers
from botka.handlers.polls import callbacks as poll_callbacks
from botka.handlers.polls import messages as poll_messages
from botka.handlers.polls.autoclose import poll_autoclose_loop


async def _run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
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
    dp.include_router(borrowed.commands.router)
    dp.include_router(shopping.commands.router)
    dp.include_router(shopping.messages.router)
    dp.include_router(shopping.callbacks.router)
    dp.include_router(borrowed.messages.router)
    dp.include_router(doors.callbacks.router)
    dp.include_router(borrowed.callbacks.router)
    dp.include_router(poll_messages.router)
    dp.include_router(poll_callbacks.router)
    dp.include_router(poll_answers.router)

    setup_dishka(container, dp)

    sessionmaker = await container.get(async_sessionmaker)
    poll_task = asyncio.create_task(poll_autoclose_loop(bot, sessionmaker))
    try:
        await dp.start_polling(bot)
    finally:
        poll_task.cancel()
        await container.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(_run())
