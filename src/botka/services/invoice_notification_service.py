from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from botka.db.models import RefinanceInvoiceNotification


class InvoiceNotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def was_notified(self, invoice_id: int) -> bool:
        return (
            await self._session.get(RefinanceInvoiceNotification, invoice_id)
            is not None
        )

    async def record(self, invoice_id: int, telegram_id: int, message_id: int) -> None:
        self._session.add(
            RefinanceInvoiceNotification(
                invoice_id=invoice_id,
                telegram_id=telegram_id,
                message_id=message_id,
            )
        )
        await self._session.commit()
