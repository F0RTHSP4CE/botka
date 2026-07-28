from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.planka_todo_publisher import PlankaTodoPublisher
from botka.services.shopping_needs_publisher import ShoppingNeedsPublisher

log = logging.getLogger(__name__)
router = Router(name=__name__)

_MEDIA_GROUP_CACHE: dict[tuple[int, str], list[int]] = {}
_MEDIA_GROUP_LOCK = asyncio.Lock()


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


def _build_pinned_fallback_text(message: Message) -> str:
    link = _build_message_link(message.chat, message.message_id)
    fallback = "📌 Pinned message"
    if link:
        fallback = f'📌 <a href="{link}">Pinned message</a>'
    return fallback


async def _send_message_content(
    bot: Bot,
    chat_id: int,
    source: Message,
    reply_markup: InlineKeyboardMarkup | None,
    caption: str | None,
    parse_mode: str | None,
) -> bool:
    if source.photo:
        await bot.send_photo(
            chat_id=chat_id,
            photo=source.photo[-1].file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.animation:
        await bot.send_animation(
            chat_id=chat_id,
            animation=source.animation.file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.video:
        await bot.send_video(
            chat_id=chat_id,
            video=source.video.file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.video_note:
        await bot.send_video_note(
            chat_id=chat_id,
            video_note=source.video_note.file_id,
            reply_markup=reply_markup,
        )
        return True
    if source.document:
        await bot.send_document(
            chat_id=chat_id,
            document=source.document.file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.audio:
        await bot.send_audio(
            chat_id=chat_id,
            audio=source.audio.file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.voice:
        await bot.send_voice(
            chat_id=chat_id,
            voice=source.voice.file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    if source.sticker:
        await bot.send_sticker(
            chat_id=chat_id,
            sticker=source.sticker.file_id,
            reply_markup=reply_markup,
        )
        return True
    return False


async def _get_media_group_ids(
    chat_id: int, media_group_id: str, fallback_message_id: int
) -> list[int]:
    async with _MEDIA_GROUP_LOCK:
        message_ids = list(_MEDIA_GROUP_CACHE.get((chat_id, media_group_id), []))
    if fallback_message_id not in message_ids:
        message_ids.append(fallback_message_id)
    message_ids = sorted(set(message_ids))
    return message_ids


async def _copy_or_resend(
    bot: Bot,
    chat_id: int,
    source: Message,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """Try ``copy_message``; on failure re-send content by type."""
    caption = source.caption or source.text
    parse_mode = "HTML" if source.caption_entities or source.entities else None

    if reply_markup is not None and await _send_message_content(
        bot,
        chat_id,
        source,
        reply_markup,
        caption,
        parse_mode,
    ):
        return

    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=source.chat.id,
            message_id=source.message_id,
            reply_markup=reply_markup,
        )
        return
    except TelegramBadRequest:
        log.debug(
            "copy_message failed for message %s in %s, falling back",
            source.message_id,
            source.chat.id,
        )

    if await _send_message_content(
        bot,
        chat_id,
        source,
        reply_markup,
        caption,
        parse_mode,
    ):
        return
    if source.text:
        await bot.send_message(
            chat_id=chat_id,
            text=source.text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=_build_pinned_fallback_text(source),
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


def _build_topic_link(chat: Chat, topic_thread_id: int) -> str | None:
    chat_id_str = str(chat.id)
    if chat.username:
        return f"https://t.me/{chat.username}/{topic_thread_id}"
    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str.removeprefix("-100")
        return f"https://t.me/c/{internal_id}/{topic_thread_id}"
    return None


@router.message(F.forum_topic_created)
@inject
async def forum_topic_created_handler(
    message: Message,
    settings: FromDishka[Settings],
) -> None:
    if settings.pins_chat_id is None:
        return

    topic = message.forum_topic_created
    if topic is None:
        return

    keyboard = _build_topic_keyboard(message)
    await message.bot.send_message(
        chat_id=settings.pins_chat_id,
        text=f"📂 <b>{html.escape(topic.name)}</b>",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


def _build_topic_keyboard(message: Message) -> InlineKeyboardMarkup | None:
    if message.message_thread_id is None:
        return None
    link = _build_topic_link(message.chat, message.message_thread_id)
    if not link:
        return None
    chat_label = (
        message.chat.title or message.chat.full_name or message.chat.username or "chat"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📂 {chat_label}", url=link)]
    ]
    author_button = _build_author_button(message)
    if author_button:
        rows.append([author_button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    key = (message.chat.id, message.media_group_id)
    async with _MEDIA_GROUP_LOCK:
        _MEDIA_GROUP_CACHE.setdefault(key, []).append(message.message_id)


@router.message(F.pinned_message)
@inject
async def pinned_message_handler(
    message: Message,
    settings: FromDishka[Settings],
    borrowed_service: FromDishka[BorrowedItemsService],
    needs_publisher: FromDishka[ShoppingNeedsPublisher],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    pinned = message.pinned_message
    if pinned is None:
        return
    if settings.pins_chat_id is None:
        return
    if await todo_publisher.is_canonical_message(
        pinned.chat.id,
        pinned.chat.username,
        pinned.message_thread_id,
        pinned.message_id,
    ):
        return
    if await needs_publisher.is_canonical_message(
        pinned.chat.id,
        pinned.message_thread_id,
        pinned.message_id,
    ):
        return
    borrowed_items = await borrowed_service.list_items_for_message(
        pinned.chat.id, pinned.message_id
    )
    if borrowed_items:
        return
    if pinned.poll is not None:
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
            try:
                copied = await message.bot.copy_messages(
                    chat_id=settings.pins_chat_id,
                    from_chat_id=pinned.chat.id,
                    message_ids=message_ids,
                )
            except TelegramBadRequest:
                log.debug(
                    "copy_messages failed for media group in %s, "
                    "falling back to single message",
                    pinned.chat.id,
                )
                footer = _build_footer_keyboard(pinned)
                await _copy_or_resend(
                    message.bot, settings.pins_chat_id, pinned, footer
                )
                return
            footer = _build_footer_keyboard(pinned)
            if footer is not None and copied:
                last_message_id = copied[-1].message_id
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=settings.pins_chat_id,
                        message_id=last_message_id,
                        reply_markup=footer,
                    )
                except TelegramBadRequest:
                    await message.bot.send_message(
                        chat_id=settings.pins_chat_id,
                        text=_build_pinned_fallback_text(pinned),
                        reply_markup=footer,
                        disable_web_page_preview=True,
                    )
            return
    footer = _build_footer_keyboard(pinned)
    await _copy_or_resend(message.bot, settings.pins_chat_id, pinned, footer)
