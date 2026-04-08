from __future__ import annotations

import logging
import time

from botka.periodic.jobs.base import PeriodicContext
from botka.services.ups_client import UpsClient

logger = logging.getLogger(__name__)

# Module-level state for tracking discharge transitions.
_was_discharging: bool = False
_last_report_ts: float = 0.0
# Timestamp when discharge was first detected but not yet confirmed.
_discharge_pending_since: float | None = None

_DISCHARGE_CONFIRM_SECONDS: float = 10.0


async def ups_discharge_report(context: PeriodicContext) -> None:
    """Check UPS state; notify on outage, periodic reminders, and power restore."""
    global _was_discharging, _last_report_ts, _discharge_pending_since

    settings = context.settings
    if not settings.ups_base_url:
        return
    if settings.ups_report_chat_id is None:
        return

    client = UpsClient(settings)
    try:
        status = await client.get_status()
    except Exception:
        logger.exception("UPS periodic check: failed to fetch status")
        return

    now = time.monotonic()
    discharging = status.is_discharging

    if discharging and not _was_discharging:
        if _discharge_pending_since is None:
            # First detection — start confirmation window
            _discharge_pending_since = now
            return
        if now - _discharge_pending_since < _DISCHARGE_CONFIRM_SECONDS:
            # Still within confirmation window, wait longer
            return
        # Confirmed: discharge persists after the confirmation delay
        _discharge_pending_since = None
        _was_discharging = True
        _last_report_ts = now
        await context.bot.send_message(
            chat_id=settings.ups_report_chat_id,
            message_thread_id=settings.ups_report_topic_id,
            text=f"⚠️ <b>Power Outage</b>\n\n{status.format_text()}",
            disable_web_page_preview=True,
        )
    elif discharging and _was_discharging:
        # Still discharging — repeat only every ups_report_interval_seconds
        if now - _last_report_ts >= settings.ups_report_interval_seconds:
            _last_report_ts = now
            await context.bot.send_message(
                chat_id=settings.ups_report_chat_id,
                message_thread_id=settings.ups_report_topic_id,
                text=f"⚠️ <b>Power Outage (ongoing)</b>\n\n{status.format_text()}",
                disable_web_page_preview=True,
            )
    else:
        # Not discharging — clear pending detection if it was a false alarm
        _discharge_pending_since = None
        if _was_discharging:
            # Transition: power restored
            _was_discharging = False
            _last_report_ts = 0.0
            await context.bot.send_message(
                chat_id=settings.ups_report_chat_id,
                message_thread_id=settings.ups_report_topic_id,
                text=f"✅ <b>Power Restored</b>\n\n{status.format_text()}",
                disable_web_page_preview=True,
            )
