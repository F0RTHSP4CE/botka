from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.polls_service import PollsService

router = Router(name=__name__)

_MEDIA_GROUP_CACHE: dict[tuple[int, str], list[int]] = {}
_MEDIA_GROUP_LOCK = asyncio.Lock()


def _is_tracked_chat(settings: Settings, chat_id: int) -> bool:
    return (
        bool(settings.pins_tracked_chat_ids)
        and chat_id in settings.pins_tracked_chat_ids
    )


def _build_message_link(chat: Chat, message_id: int) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_id_str = str(chat.id)
    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str.removeprefix("-100")
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def _build_go_to_button(message: Message) -> InlineKeyboardButton | None:
    link = _build_message_link(message.chat, message.message_id)
    if not link:
        return None
    chat_label = (
        message.chat.title or message.chat.full_name or message.chat.username or "chat"
    )
    text = f"📌 {chat_label}"
    return InlineKeyboardButton(text=text, url=link)


def _build_author_button(message: Message) -> InlineKeyboardButton | None:
    if message.from_user is not None:
        label = message.from_user.full_name or "Author"
        return InlineKeyboardButton(
            text=label, url=f"tg://user?id={message.from_user.id}"
        )
    if message.sender_chat is not None and message.sender_chat.username:
        label = message.sender_chat.title or message.sender_chat.username
        return InlineKeyboardButton(
            text=label,
            url=f"https://t.me/{message.sender_chat.username}",
        )
    return None


def _build_footer_keyboard(message: Message) -> InlineKeyboardMarkup | None:
    go_to_button = _build_go_to_button(message)
    author_button = _build_author_button(message)
    rows: list[list[InlineKeyboardButton]] = []
    if go_to_button:
        rows.append([go_to_button])
    if author_button:
        rows.append([author_button])
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_poll_preview(message: Message) -> str:
    poll = message.poll
    if poll is None:
        return ""
    lines = [f"📊 <b>{html.escape(poll.question)}</b>"]
    if poll.options:
        for option in poll.options:
            lines.append(f"• {html.escape(option.text)}")
    return "\n".join(lines)


async def _get_media_group_ids(
    chat_id: int, media_group_id: str, fallback_message_id: int
) -> list[int]:
    async with _MEDIA_GROUP_LOCK:
        message_ids = list(_MEDIA_GROUP_CACHE.get((chat_id, media_group_id), []))
    if fallback_message_id not in message_ids:
        message_ids.append(fallback_message_id)
    message_ids = sorted(set(message_ids))
    return message_ids


@router.message(F.media_group_id)
@inject
async def track_media_group_messages(
    message: Message,
    settings: FromDishka[Settings],
) -> None:
    if message.media_group_id is None:
        return
    if settings.pins_chat_id is None:
        return
    if not _is_tracked_chat(settings, message.chat.id):
        return
    key = (message.chat.id, message.media_group_id)
    async with _MEDIA_GROUP_LOCK:
        _MEDIA_GROUP_CACHE.setdefault(key, []).append(message.message_id)


@router.message(F.pinned_message)
@inject
async def pinned_message_handler(
    message: Message,
    settings: FromDishka[Settings],
    borrowed_service: FromDishka[BorrowedItemsService],
    polls_service: FromDishka[PollsService],
) -> None:
    pinned = message.pinned_message
    if pinned is None:
        return
    if settings.pins_chat_id is None:
        return
    if not _is_tracked_chat(settings, message.chat.id):
        return
    borrowed_items = await borrowed_service.list_items_for_message(
        pinned.chat.id, pinned.message_id
    )
    if borrowed_items:
        return
    if pinned.poll is not None:
        poll = await polls_service.get_poll(pinned.poll.id)
        if poll is not None:
            preview = _format_poll_preview(pinned)
            footer = _build_footer_keyboard(pinned)
            await message.bot.send_message(
                chat_id=settings.pins_chat_id,
                text=preview,
                reply_markup=footer,
                disable_web_page_preview=True,
            )
            return
    if pinned.media_group_id:
        message_ids = await _get_media_group_ids(
            pinned.chat.id,
            pinned.media_group_id,
            pinned.message_id,
        )
        if message_ids:
            copied = await message.bot.copy_messages(
                chat_id=settings.pins_chat_id,
                from_chat_id=pinned.chat.id,
                message_ids=message_ids,
            )
            footer = _build_footer_keyboard(pinned)
            if footer is not None and copied:
                last_message_id = copied[-1].message_id
                await message.bot.edit_message_reply_markup(
                    chat_id=settings.pins_chat_id,
                    message_id=last_message_id,
                    reply_markup=footer,
                )
            return
    footer = _build_footer_keyboard(pinned)
    await message.bot.copy_message(
        chat_id=settings.pins_chat_id,
        from_chat_id=pinned.chat.id,
        message_id=pinned.message_id,
        reply_markup=footer,
    )
