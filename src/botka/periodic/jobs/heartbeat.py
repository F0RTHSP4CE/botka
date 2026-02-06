from __future__ import annotations

from botka.periodic.jobs.base import PeriodicContext


async def send_heartbeat(context: PeriodicContext) -> None:
    if context.settings.heartbeat_chat_id is None:
        return
    await context.bot.send_message(
        chat_id=context.settings.heartbeat_chat_id,
        message_thread_id=context.settings.heartbeat_topic_id,
        text="Periodic heartbeat.",
        disable_notification=True,
        disable_web_page_preview=True,
    )
