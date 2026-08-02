from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import botka.handlers.menu as menu_handler
import botka.handlers.planka.menu as planka_menu
import botka.handlers.refinance.menu as refinance_menu
from botka.config import Settings
from botka.db.session import init_models
from botka.di.container import build_container
from botka.handlers import (
    anonymous_admins,
    bambu,
    borrowed,
    doors,
    fridge,
    help,
    mac_tracker,
    meeting,
    periodic,
    pins,
    planka,
    refinance,
    shopping,
    ups,
    users,
)
from botka.handlers.help.commands import get_bot_commands
from botka.handlers.polls import answers as poll_answers
from botka.handlers.polls import commands as poll_commands
from botka.handlers.polls import messages as poll_messages
from botka.mac_tracker.web import run_mac_tracker_server
from botka.middlewares import MediaGroupCollectorMiddleware, UserSyncMiddleware
from botka.periodic import periodic_loop
from botka.services.mac_tracker_service import mac_tracker_poll_loop
from botka.services.planka_client import PlankaClient
from botka.services.planka_command_service import PlankaCommandService
from botka.services.planka_notification_service import PlankaNotificationService
from botka.services.planka_poller import run_planka_poller
from botka.services.planka_todo_publisher import PlankaTodoPublisher
from botka.services.shopping_list_service import ShoppingListService
from botka.services.shopping_needs_publisher import ShoppingNeedsPublisher


async def _run() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.mac_tracker_jwt_secret:
        settings.mac_tracker_jwt_secret = secrets.token_urlsafe(48)

    # Routing topology lives here — each router is scoped to its chat/topic
    # before being registered, so handler bodies contain only domain logic.
    if settings.borrowed_topic_id is not None:
        borrowed.messages.router.message.filter(
            F.message_thread_id == settings.borrowed_topic_id
        )
        if settings.borrowed_chat_id is not None:
            borrowed.messages.router.message.filter(
                F.chat.id == settings.borrowed_chat_id
            )
    if settings.shopping_topic_id is not None:
        shopping.messages.router.message.filter(
            F.message_thread_id == settings.shopping_topic_id
        )
        if settings.shopping_chat_id is not None:
            shopping.messages.router.message.filter(
                F.chat.id == settings.shopping_chat_id
            )
    pins.messages.router.message.filter(F.chat.id.in_(settings.pins_tracked_chat_ids))

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
    dp.include_router(anonymous_admins.commands.router)
    dp.include_router(menu_handler.router)
    dp.include_router(refinance.commands.router)
    dp.include_router(refinance.callbacks.router)
    dp.include_router(refinance_menu.router)
    dp.include_router(doors.commands.router)
    dp.include_router(fridge.commands.router)
    dp.include_router(mac_tracker.commands.router)
    dp.include_router(mac_tracker.callbacks.router)
    dp.include_router(borrowed.commands.router)
    dp.include_router(shopping.commands.router)
    dp.include_router(meeting.commands.router)
    dp.include_router(poll_commands.router)
    dp.include_router(shopping.messages.router)
    dp.include_router(shopping.callbacks.router)
    dp.include_router(borrowed.messages.router)
    dp.include_router(planka_menu.router)
    dp.include_router(planka.commands.router)
    dp.include_router(ups.commands.router)
    dp.include_router(bambu.commands.router)
    dp.include_router(bambu.callbacks.router)
    dp.include_router(doors.callbacks.router)
    dp.include_router(fridge.callbacks.router)
    dp.include_router(borrowed.callbacks.router)
    dp.include_router(pins.messages.router)
    dp.include_router(poll_messages.router)
    dp.include_router(poll_answers.router)
    dp.include_router(periodic.commands.router)

    setup_dishka(container, dp)

    sessionmaker = await container.get(async_sessionmaker)
    user_sync = UserSyncMiddleware(sessionmaker, settings)
    dp.message.middleware(MediaGroupCollectorMiddleware())
    dp.message.middleware(user_sync)
    dp.edited_message.middleware(user_sync)
    dp.callback_query.middleware(user_sync)
    dp.poll_answer.middleware(user_sync)
    planka_client = await container.get(PlankaClient)
    planka_notifications = await container.get(PlankaNotificationService)
    todo_publisher = await container.get(PlankaTodoPublisher)
    needs_publisher = await container.get(ShoppingNeedsPublisher)

    async def _refresh_todo_topics() -> None:
        async with container() as request_container:
            planka_service = await request_container.get(PlankaCommandService)
            await todo_publisher.refresh(bot, planka_service)

    async def _refresh_shopping_list() -> None:
        async with container() as request_container:
            shopping_service = await request_container.get(ShoppingListService)
            await needs_publisher.refresh(bot, shopping_service)

    background_tasks = (
        asyncio.create_task(mac_tracker_poll_loop(bot, sessionmaker, settings)),
        asyncio.create_task(run_mac_tracker_server(settings, sessionmaker)),
        asyncio.create_task(periodic_loop(bot, sessionmaker, settings)),
        asyncio.create_task(
            run_planka_poller(
                bot,
                planka_client,
                settings,
                planka_notifications,
                todo_refresh=_refresh_todo_topics,
            )
        ),
    )
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands(get_bot_commands())
    for name, refresh in (
        ("todo", _refresh_todo_topics),
        ("shopping", _refresh_shopping_list),
    ):
        try:
            await refresh()
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to initialize canonical %s message", name
            )
    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await container.close()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())
