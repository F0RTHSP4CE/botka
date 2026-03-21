from __future__ import annotations

import asyncio
import html
import io
import logging

from aiogram import F, Router
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.borrowed.utils import build_return_keyboard
from botka.handlers.user_links import format_user_link
from botka.services.borrowed_item_detector import BorrowedItemDetector
from botka.services.borrowed_items_service import BorrowedItemsService

router = Router(name=__name__)

logger = logging.getLogger(__name__)

_MEDIA_GROUP_CACHE: dict[str, list[tuple[Message, bytes | None, str | None]]] = {}
_MEDIA_GROUP_TASKS: dict[str, asyncio.Task[None]] = {}
_MEDIA_GROUP_LOCK = asyncio.Lock()


async def _download_photo(message: Message) -> tuple[bytes | None, str | None]:
    if not message.photo:
        return None, None
    photo = message.photo[-1]
    try:
        buffer = io.BytesIO()
        await message.bot.download(photo, destination=buffer)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        return None, None


async def _handle_borrowed_entry(
    message: Message,
    text: str,
    images: list[tuple[bytes, str]],
    borrowed_service: BorrowedItemsService,
    detector: BorrowedItemDetector,
) -> None:
    if message.from_user is None:
        return
    item_names = await detector.detect_item_names(text, images)
    if not item_names:
        logger.info(
            "No borrowed items detected (chat_id=%s, message_id=%s, media=%s).",
            message.chat.id,
            message.message_id,
            bool(images),
        )
        return
    items = []
    for item_name in item_names:
        item = await borrowed_service.add_item(
            actor_telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            item_name=item_name,
        )
        items.append(item)

    actor = format_user_link(message.from_user)
    if len(items) == 1:
        item_text = html.escape(items[0].item_name)
        message_text = f"📌 Borrowed <b>{item_text}</b> by {actor}"
    else:
        lines = "\n".join(f"• <b>{html.escape(item.item_name)}</b>" for item in items)
        message_text = f"📌 Borrowed items by {actor}:\n{lines}"
    await message.bot.send_message(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=message_text,
        reply_markup=build_return_keyboard(
            [(item.id, item.item_name, item.returned) for item in items]
        ),
        disable_web_page_preview=True,
    )
    await message.bot.pin_chat_message(
        chat_id=message.chat.id,
        message_id=message.message_id,
        disable_notification=True,
    )


async def _process_media_group(
    group_id: str,
    borrowed_service: BorrowedItemsService,
    detector: BorrowedItemDetector,
) -> None:
    await asyncio.sleep(1.2)
    async with _MEDIA_GROUP_LOCK:
        entries = _MEDIA_GROUP_CACHE.pop(group_id, [])
        _MEDIA_GROUP_TASKS.pop(group_id, None)
    if not entries:
        return
    entries_sorted = sorted(entries, key=lambda item: item[0].message_id)
    base_message = entries_sorted[0][0]
    text = ""
    for msg, _, _ in entries_sorted:
        text = (msg.text or msg.caption or "").strip()
        if text:
            break
    images: list[tuple[bytes, str]] = []
    for _, image_bytes, image_mime in entries_sorted:
        if image_bytes:
            images.append((image_bytes, image_mime or "image/jpeg"))
    await _handle_borrowed_entry(
        base_message,
        text,
        images,
        borrowed_service,
        detector,
    )


@router.message(
    (F.text | F.photo) & ~F.text.startswith("/") & ~F.caption.startswith("/")
)
@inject
async def borrowed_message_handler(
    message: Message,
    borrowed_service: FromDishka[BorrowedItemsService],
    detector: FromDishka[BorrowedItemDetector],
) -> None:
    if message.from_user is None:
        return
    group_id = message.media_group_id
    if group_id:
        image_bytes, image_mime = await _download_photo(message)
        async with _MEDIA_GROUP_LOCK:
            _MEDIA_GROUP_CACHE.setdefault(group_id, []).append(
                (message, image_bytes, image_mime)
            )
            if group_id not in _MEDIA_GROUP_TASKS:
                _MEDIA_GROUP_TASKS[group_id] = asyncio.create_task(
                    _process_media_group(group_id, borrowed_service, detector)
                )
        return
    image_bytes, image_mime = await _download_photo(message)
    images: list[tuple[bytes, str]] = []
    if image_bytes:
        images.append((image_bytes, image_mime or "image/jpeg"))
    await _handle_borrowed_entry(
        message,
        message.text or message.caption,
        images,
        borrowed_service,
        detector,
    )
