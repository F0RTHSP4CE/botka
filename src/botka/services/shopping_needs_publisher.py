from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from botka.config import Settings
from botka.db.models import ShoppingItem, ShoppingNeedsPin
from botka.services.shopping_list_service import ShoppingListService
from botka.services.telegram_retry import call_with_retry_after

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ShoppingNeedsView:
    items: tuple[ShoppingItem, ...]

    @property
    def text(self) -> str:
        if not self.items:
            return "Shopping list is empty."
        lines = ["<b>Shopping list:</b>"]
        lines.extend(f"- {html.escape(item.text)}" for item in self.items)
        return "\n".join(lines)

    @property
    def keyboard(self) -> InlineKeyboardMarkup | None:
        if not self.items:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=item.text[:64],
                        callback_data=f"buy:{item.id}",
                    )
                ]
                for item in self.items
            ]
        )


@dataclass(frozen=True, slots=True)
class ShoppingNeedsPublication:
    published: bool
    link: str | None = None


class ShoppingNeedsPublisher:
    """Maintain the canonical pinned shopping list in its configured topic."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._chat_id = settings.shopping_chat_id
        self._topic_id = settings.shopping_topic_id
        self._lock = asyncio.Lock()

    @property
    def has_target(self) -> bool:
        return self._chat_id is not None and self._topic_id is not None

    async def load(self, service: ShoppingListService) -> ShoppingNeedsView:
        return ShoppingNeedsView(tuple(await service.list_open_items()))

    async def refresh(
        self, bot: Bot, service: ShoppingListService
    ) -> ShoppingNeedsPublication:
        if not self.has_target:
            return ShoppingNeedsPublication(False)
        return await self.publish(bot, await self.load(service))

    async def refresh_safely(
        self, bot: Bot, service: ShoppingListService
    ) -> None:
        try:
            await self.refresh(bot, service)
        except Exception:
            logger.exception("Failed to refresh the canonical shopping list")

    async def publish(
        self, bot: Bot, view: ShoppingNeedsView
    ) -> ShoppingNeedsPublication:
        if self._chat_id is None or self._topic_id is None:
            return ShoppingNeedsPublication(False)

        async with self._lock:
            try:
                return await self._publish_locked(bot, view)
            except Exception:
                logger.exception(
                    "Cannot publish the canonical shopping list in %s",
                    self._chat_id,
                )
                return ShoppingNeedsPublication(False)

    async def _publish_locked(
        self, bot: Bot, view: ShoppingNeedsView
    ) -> ShoppingNeedsPublication:
        message_id = await self._get_message_id()
        if message_id is not None:
            try:
                await call_with_retry_after(
                    lambda: bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=message_id,
                        text=view.text,
                        parse_mode="HTML",
                        reply_markup=view.keyboard,
                    ),
                    description=f"Shopping-list update in {self._chat_id}",
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    await self._save_message_id(None)
                    message_id = None
            except TelegramAPIError:
                logger.warning(
                    "Cannot update the canonical shopping list in %s",
                    self._chat_id,
                    exc_info=True,
                )
                return ShoppingNeedsPublication(False)
            if message_id is not None:
                return ShoppingNeedsPublication(
                    True, self._message_link(message_id)
                )

        sent = await bot.send_message(
            chat_id=self._chat_id,
            message_thread_id=self._topic_id,
            text=view.text,
            parse_mode="HTML",
            reply_markup=view.keyboard,
        )
        message_id = sent.message_id
        await self._save_message_id(message_id)

        try:
            await bot.pin_chat_message(
                chat_id=self._chat_id,
                message_id=message_id,
                disable_notification=True,
            )
        except Exception:
            logger.warning(
                "Could not pin shopping list message %s in %s",
                message_id,
                self._chat_id,
                exc_info=True,
            )
        return ShoppingNeedsPublication(True, self._message_link(message_id))

    async def is_canonical_message(
        self, chat_id: int, thread_id: int | None, message_id: int
    ) -> bool:
        if (
            chat_id != self._chat_id
            or (thread_id or 0) != (self._topic_id or 0)
        ):
            return False
        try:
            return await self._get_message_id() == message_id
        except Exception:
            logger.exception("Cannot check the canonical shopping-list message")
            return False

    async def _get_message_id(self) -> int | None:
        async with self._sessionmaker() as session:
            row = await self._find_message(session)
            return row.message_id if row else None

    async def _save_message_id(self, message_id: int | None) -> None:
        async with self._sessionmaker() as session:
            row = await self._find_message(session)
            if message_id is None:
                if row is not None:
                    await session.delete(row)
            elif row is None:
                session.add(
                    ShoppingNeedsPin(
                        chat_id=self._chat_id,
                        topic_id=self._topic_id,
                        message_id=message_id,
                    )
                )
            else:
                row.message_id = message_id
            await session.commit()

    async def _find_message(
        self, session: AsyncSession
    ) -> ShoppingNeedsPin | None:
        result = await session.execute(
            select(ShoppingNeedsPin).where(
                ShoppingNeedsPin.chat_id == self._chat_id,
                ShoppingNeedsPin.topic_id == self._topic_id,
            )
        )
        return result.scalar_one_or_none()

    def _message_link(self, message_id: int) -> str | None:
        value = str(self._chat_id)
        if value.startswith("-100") and value[4:].isdigit():
            return f"https://t.me/c/{value[4:]}/{message_id}"
        return None
