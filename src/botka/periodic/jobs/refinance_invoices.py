from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from botka.db.models import User
from botka.handlers.refinance.invoice_ui import (
    format_notification,
    notification_keyboard,
)
from botka.periodic.jobs.base import PeriodicContext
from botka.services.invoice_notification_service import InvoiceNotificationService
from botka.services.refinance_client import RefinanceClient

logger = logging.getLogger(__name__)


def _is_new_invoice(invoice: dict, poll_seconds: int) -> bool:
    raw_created_at = invoice.get("created_at")
    if not raw_created_at:
        return False
    try:
        created_at = datetime.fromisoformat(str(raw_created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    recent_window = timedelta(seconds=max(poll_seconds * 2, 120))
    return created_at >= datetime.now(timezone.utc) - recent_window


async def notify_refinance_invoices(context: PeriodicContext) -> None:
    client = RefinanceClient(context.settings)
    if not client.is_configured:
        return
    try:
        invoices = await client.get_pending_fee_invoices()
    except Exception:
        logger.exception("Failed to poll pending refinance invoices")
        return

    async with context.sessionmaker() as session:
        receipts = InvoiceNotificationService(session)
        local_telegram_ids = set(
            (await session.scalars(select(User.telegram_id))).all()
        )
        for invoice in invoices:
            invoice_id = int(invoice["id"])
            if await receipts.was_notified(invoice_id):
                continue
            raw_telegram_id = (
                (invoice.get("from_entity") or {}).get("auth") or {}
            ).get("telegram_id")
            try:
                telegram_id = int(raw_telegram_id)
            except (TypeError, ValueError):
                logger.warning("Invoice %s payer has no valid Telegram ID", invoice_id)
                continue
            if telegram_id not in local_telegram_ids:
                logger.info(
                    "Skipping invoice %s notification: Telegram user %s is not in Botka",
                    invoice_id,
                    telegram_id,
                )
                continue
            try:
                sent = await context.bot.send_message(
                    chat_id=telegram_id,
                    text=format_notification(
                        invoice,
                        is_new=_is_new_invoice(
                            invoice,
                            context.settings.refinance_invoice_poll_seconds,
                        ),
                    ),
                    reply_markup=notification_keyboard(invoice, telegram_id),
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception(
                    "Failed to notify Telegram user %s about invoice %s",
                    telegram_id,
                    invoice_id,
                )
                continue
            await receipts.record(invoice_id, telegram_id, sent.message_id)
