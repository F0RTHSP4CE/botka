from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from botka.config import Settings
from botka.db.models import PlankaTodoMessage
from botka.services.planka_command_service import (
    CardEntry,
    PlankaCommandService,
    TodoSections,
)
from botka.services.telegram_retry import call_with_retry_after

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TodoTarget:
    chat_id: str
    thread_id: int | None

    @property
    def topic_id(self) -> int:
        return self.thread_id or 0

    def matches(
        self, chat_id: int, chat_username: str | None, thread_id: int | None
    ) -> bool:
        username = (chat_username or "").lstrip("@").casefold()
        configured = self.chat_id.strip()
        matches_chat = configured == str(chat_id) or (
            configured.startswith("@")
            and bool(username)
            and configured[1:].casefold() == username
        )
        return matches_chat and self.topic_id == (thread_id or 0)

    def message_link(self, message_id: int) -> str | None:
        value = self.chat_id.strip()
        if value.startswith("@"):
            return f"https://t.me/{value[1:]}/{message_id}"
        if value.startswith("-100") and value[4:].isdigit():
            return f"https://t.me/c/{value[4:]}/{message_id}"
        return None


@dataclass(frozen=True, slots=True)
class TodoView:
    available: tuple[CardEntry, ...]
    in_progress: tuple[CardEntry, ...]

    @classmethod
    def from_sections(cls, sections: TodoSections) -> TodoView:
        return cls(sections.available, sections.in_progress)

    @property
    def text(self) -> str:
        lines = ["📜 <b>Quests:</b>"]
        lines.extend(self._entry_lines(self.available))
        lines.extend(["", "<b>In progress:</b>"])
        lines.extend(self._entry_lines(self.in_progress))
        lines.extend(["", "Press on the quest to view &amp; take it:"])
        return "\n".join(lines)

    @property
    def keyboard(self) -> InlineKeyboardMarkup:
        buttons = [
            self._button(entry, in_progress=False) for entry in self.available
        ]
        buttons.extend(
            self._button(entry, in_progress=True) for entry in self.in_progress
        )
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def _attachment_emojis(entry: CardEntry) -> str:
        return (" 🖼" if entry.has_images else "") + (
            " 📎" if entry.has_other_attachments else ""
        )

    @classmethod
    def _entry_lines(cls, entries: tuple[CardEntry, ...]) -> list[str]:
        return [
            f"- {html.escape(entry.name)}{cls._attachment_emojis(entry)}"
            for entry in entries
        ] or ["- (none)"]

    @classmethod
    def _button(
        cls, entry: CardEntry, *, in_progress: bool
    ) -> list[InlineKeyboardButton]:
        prefix = "⚔️ " if in_progress else ""
        return [
            InlineKeyboardButton(
                text=f"{prefix}{entry.name}{cls._attachment_emojis(entry)}"[:64],
                callback_data=f"pquest:view:{entry.short_id}",
            )
        ]


@dataclass(frozen=True, slots=True)
class TodoPublication:
    published: int
    links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PublishedMessage:
    link: str | None


class PlankaTodoPublisher:
    """Render and maintain one canonical pinned todo list per configured target."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._targets = tuple(
            TodoTarget(chat_id, thread_id)
            for chat_id, thread_id in settings.get_planka_notification_targets()
        )
        self._lock = asyncio.Lock()

    @property
    def has_targets(self) -> bool:
        return bool(self._targets)

    async def load(self, svc: PlankaCommandService) -> TodoView:
        return TodoView.from_sections(await svc.list_todos())

    async def refresh(
        self, bot: Bot, svc: PlankaCommandService
    ) -> TodoPublication:
        if not self.has_targets or not svc.is_configured or not svc.todo_list_id:
            return TodoPublication(0)
        return await self.publish(bot, await self.load(svc))

    async def refresh_safely(self, bot: Bot, svc: PlankaCommandService) -> None:
        try:
            await self.refresh(bot, svc)
        except Exception:
            logger.exception("Failed to refresh canonical todo-topic messages")

    async def is_canonical_message(
        self,
        chat_id: int,
        chat_username: str | None,
        thread_id: int | None,
        message_id: int,
    ) -> bool:
        """Check whether a Telegram message is one of the maintained todo lists."""
        for target in self._targets:
            if target.matches(chat_id, chat_username, thread_id):
                return await self._get_message_id(target) == message_id
        return False

    async def publish(self, bot: Bot, view: TodoView) -> TodoPublication:
        links: list[str] = []
        published = 0
        async with self._lock:
            for target in self._targets:
                try:
                    result = await self._publish_target(bot, target, view)
                except Exception:
                    logger.exception(
                        "Cannot publish canonical todo message in %s",
                        target.chat_id,
                    )
                    continue
                if result is not None:
                    published += 1
                    if result.link:
                        links.append(result.link)
        return TodoPublication(published, tuple(links))

    async def _publish_target(
        self, bot: Bot, target: TodoTarget, view: TodoView
    ) -> _PublishedMessage | None:
        message_id = await self._get_message_id(target)
        if message_id is not None:
            try:
                await call_with_retry_after(
                    lambda: bot.edit_message_text(
                        chat_id=target.chat_id,
                        message_id=message_id,
                        text=view.text,
                        parse_mode="HTML",
                        reply_markup=view.keyboard,
                        link_preview_options={"is_disabled": True},
                    ),
                    description=f"Todo update in {target.chat_id}",
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    await self._save_message_id(target, None)
                    message_id = None
            except TelegramForbiddenError:
                logger.warning(
                    "Cannot update canonical todo message in %s", target.chat_id
                )
                return None
            except TelegramAPIError:
                logger.warning(
                    "Cannot update canonical todo message in %s",
                    target.chat_id,
                    exc_info=True,
                )
                return None
            if message_id is not None:
                return _PublishedMessage(target.message_link(message_id))

        try:
            sent = await bot.send_message(
                chat_id=target.chat_id,
                message_thread_id=target.thread_id,
                text=view.text,
                parse_mode="HTML",
                reply_markup=view.keyboard,
                link_preview_options={"is_disabled": True},
            )
        except Exception:
            logger.exception(
                "Cannot create canonical todo message in %s", target.chat_id
            )
            return None
        message_id = sent.message_id
        await self._save_message_id(target, message_id)

        try:
            await bot.pin_chat_message(
                chat_id=target.chat_id,
                message_id=message_id,
                disable_notification=True,
            )
        except Exception:
            logger.warning(
                "Could not pin canonical todo message %s in %s",
                message_id,
                target.chat_id,
                exc_info=True,
            )
        return _PublishedMessage(target.message_link(message_id))

    async def _get_message_id(self, target: TodoTarget) -> int | None:
        async with self._sessionmaker() as session:
            row = await self._find_message(session, target)
            return row.message_id if row else None

    async def _save_message_id(
        self, target: TodoTarget, message_id: int | None
    ) -> None:
        async with self._sessionmaker() as session:
            row = await self._find_message(session, target)
            if message_id is None:
                if row is not None:
                    await session.delete(row)
            elif row is None:
                session.add(
                    PlankaTodoMessage(
                        target_chat_id=target.chat_id,
                        topic_id=target.topic_id,
                        message_id=message_id,
                    )
                )
            else:
                row.message_id = message_id
            await session.commit()

    @staticmethod
    async def _find_message(
        session: AsyncSession, target: TodoTarget
    ) -> PlankaTodoMessage | None:
        result = await session.execute(
            select(PlankaTodoMessage).where(
                PlankaTodoMessage.target_chat_id == target.chat_id,
                PlankaTodoMessage.topic_id == target.topic_id,
            )
        )
        return result.scalar_one_or_none()
