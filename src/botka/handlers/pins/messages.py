from __future__ import annotations

import asyncio
import html

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from dishka.integrations.aiogram import FromDishka, inject

from botka.config import Settings

log = logging.getLogger(__name__)
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.polls_service import PollsService
from botka.services.shopping_list_service import ShoppingListService

router = Router(name=__name__)

_MEDIA_GROUP_CACHE: dict[tuple[int, str], list[int]] = {}
_MEDIA_GROUP_LOCK = asyncio.Lock()

# (chat_id, thread_id) -> topic name — tracks topics awaiting their first content message
_PENDING_NEW_TOPICS: dict[tuple[int, int], str] = {}

# Media-group batching for new-topic forwarding.
# key = media_group_id -> (topic_name, first Message, list of message_ids)
_NEW_TOPIC_MG_CACHE: dict[str, tuple[str, Message, list[int]]] = {}
_NEW_TOPIC_MG_LOCK = asyncio.Lock()
_NEW_TOPIC_MG_TASKS: dict[str, asyncio.Task[None]] = {}
_NEW_TOPIC_MG_DELAY = 1.5  # seconds to wait for remaining album items


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

    # Remember this thread so the next content message gets forwarded too.
    if message.message_thread_id is not None:
        _PENDING_NEW_TOPICS[(message.chat.id, message.message_thread_id)] = topic.name


def _build_topic_keyboard(
    message: Message, topic_name: str
) -> InlineKeyboardMarkup | None:
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


async def _flush_new_topic_media_group(
    media_group_id: str,
    bot: Bot,
    pins_chat_id: int,
) -> None:
    """Wait for album messages to accumulate, then forward them all."""
    await asyncio.sleep(_NEW_TOPIC_MG_DELAY)
    async with _NEW_TOPIC_MG_LOCK:
        entry = _NEW_TOPIC_MG_CACHE.pop(media_group_id, None)
        _NEW_TOPIC_MG_TASKS.pop(media_group_id, None)
    if entry is None:
        return
    topic_name, first_message, message_ids = entry
    message_ids = sorted(set(message_ids))
    keyboard = _build_topic_keyboard(first_message, topic_name)
    try:
        await bot.copy_messages(
            chat_id=pins_chat_id,
            from_chat_id=first_message.chat.id,
            message_ids=message_ids,
        )
        # Album photos can't carry inline keyboards — send a
        # separate message with the buttons.
        if keyboard:
            topic_label = html.escape(topic_name)
            await bot.send_message(
                chat_id=pins_chat_id,
                text=f"📂 <b>{topic_label}</b>",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
    except TelegramBadRequest:
        log.debug("copy_messages failed for new topic album, falling back")
        keyboard = _build_topic_keyboard(first_message, topic_name)
        await _copy_or_resend(bot, pins_chat_id, first_message, keyboard)


class NewTopicForwardMiddleware(BaseMiddleware):
    """Forwards the first content message in a newly created topic to pins.

    Runs as a middleware so it doesn't consume the event — other handlers
    (borrowed, shopping, etc.) still process the message normally.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and (
            self._settings.pins_chat_id is not None
            and event.message_thread_id is not None
            and not event.forum_topic_created
            and not event.forum_topic_edited
            and not event.forum_topic_closed
            and not event.forum_topic_reopened
            and not event.pinned_message
        ):
            thread_key = (event.chat.id, event.message_thread_id)
            topic_name = _PENDING_NEW_TOPICS.pop(thread_key, None)

            if topic_name is not None and event.media_group_id:
                # First message of a new-topic album — start collecting.
                async with _NEW_TOPIC_MG_LOCK:
                    _NEW_TOPIC_MG_CACHE[event.media_group_id] = (
                        topic_name,
                        event,
                        [event.message_id],
                    )
                    _NEW_TOPIC_MG_TASKS[event.media_group_id] = asyncio.create_task(
                        _flush_new_topic_media_group(
                            event.media_group_id,
                            event.bot,
                            self._settings.pins_chat_id,
                        )
                    )
            elif topic_name is None and event.media_group_id:
                # Subsequent album message — append if we're tracking this group.
                async with _NEW_TOPIC_MG_LOCK:
                    entry = _NEW_TOPIC_MG_CACHE.get(event.media_group_id)
                    if entry is not None:
                        entry[2].append(event.message_id)
            elif topic_name is not None:
                # Single (non-album) message — forward immediately.
                keyboard = _build_topic_keyboard(event, topic_name)
                try:
                    await _copy_or_resend(
                        event.bot, self._settings.pins_chat_id, event, keyboard
                    )
                except Exception:
                    log.exception("Failed to forward new topic content message")
        return await handler(event, data)


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
    polls_service: FromDishka[PollsService],
    shopping_service: FromDishka[ShoppingListService],
) -> None:
    pinned = message.pinned_message
    if pinned is None:
        return
    if settings.pins_chat_id is None:
        return
    if (
        settings.shopping_chat_id is not None
        and settings.shopping_topic_id is not None
        and pinned.chat.id == settings.shopping_chat_id
        and pinned.message_thread_id == settings.shopping_topic_id
    ):
        needs_message_id = await shopping_service.get_needs_message_id(
            settings.shopping_chat_id,
            settings.shopping_topic_id,
        )
        if needs_message_id == pinned.message_id:
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
