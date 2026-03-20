from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import time
from typing import TypeVar

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User, UserTier
from botka.services.planka_client import PlankaAttachment, PlankaAuthError, PlankaClientError, PlankaList, PlankaTaskList
from botka.services.planka_attachment_cache_service import PlankaAttachmentCacheService
from botka.services.planka_command_service import (
    AttachFileResult,
    CardDetailResult,
    CardEntry,
    CreateTodoResult,
    MoveTaskResult,
    PlankaCardNotFoundError,
    PlankaCommandService,
    PlankaListNotConfiguredError,
)

router = Router(name=__name__)
logger = logging.getLogger(__name__)

_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_ATTACH_MEDIA_GROUP_TTL_SECONDS = 600.0

T = TypeVar("T")

_ATTACH_MEDIA_GROUP_CACHE: dict[tuple[int, str], tuple[float, list[Message]]] = {}
_ATTACH_MEDIA_GROUP_LOCK = asyncio.Lock()


# --- Handlers ---

@router.message(Command("boards"))
@inject
async def boards_command(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        boards = await svc.list_boards()
        if not boards:
            await loading_msg.delete()
            await message.reply("No boards were found for this Planka account.", disable_web_page_preview=True, disable_notification=True)
            return
        board_list_results = await asyncio.gather(
            *[svc.get_board_lists(b.id) for b in boards[:20]],
            return_exceptions=True,
        )
        board_lists: dict[str, list[PlankaList]] = {}
        for b, result in zip(boards[:20], board_list_results):
            if isinstance(result, BaseException):
                board_lists[b.id] = []
            else:
                board_lists[b.id] = result
    except PlankaClientError as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return
    all_lines: list[str] = ["<b>Your boards:</b>"]
    for b in boards[:20]:
        board_url = f"{svc.base_url}/boards/{b.id}" if svc.base_url else ""
        board_link = (
            f'<a href="{html.escape(board_url)}">{html.escape(b.name)}</a>'
            if board_url
            else f"<b>{html.escape(b.name)}</b>"
        )
        all_lines.append(f"\n{board_link} (id: <code>{html.escape(b.id)}</code>)")
        lists = board_lists.get(b.id, [])
        if lists:
            for lst in lists:
                all_lines.append(f"  - {html.escape(lst.name)} (id: <code>{html.escape(lst.id)}</code>)")
        else:
            all_lines.append("  (no lists)")
    await loading_msg.delete()
    await _reply_chunked(message, all_lines)


@router.message(Command("todo"))
@inject
async def todo_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    album: list[Message] | None = None,
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    if not svc.todo_list_id:
        await message.reply("BOTKA_PLANKA_TODO_LIST_ID is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        if not args:
            sections = await svc.list_todos()
            await loading_msg.delete()
            await _send_todo_list(message, sections, svc.base_url)
            return
        first_word = args.split()[0]
        if first_word.isdigit():
            card_id = await svc.resolve_card_id(first_word)
            if card_id:
                actor = (message.from_user.id, message.from_user.username) if message.from_user else None
                result = await svc.move_task(first_word, svc.todo_list_id, actor=actor, position_at_top=True)
                await loading_msg.delete()
                await _send_move_reply(message, first_word, result, "moved back to TODO", svc.base_url)
                return
        card_name, card_description, checklist_groups = _parse_todo_args(args)
        album_messages = album or [message]
        album_photos = [m.photo[-1] for m in album_messages if m.photo]
        photo_data: tuple[str, bytes] | None = None
        if album_photos:
            first_photo = album_photos[0]
            data = await _download_telegram_file_bytes(message, first_photo)
            if data:
                photo_data = (f"{first_photo.file_unique_id}.jpg", data)
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.create_todo(
            card_name,
            [],
            svc.todo_list_id,
            checklist_groups=checklist_groups,
            description=card_description,
            actor=actor,
            photo_data=photo_data,
            media_group_id=message.media_group_id,
        )

        # Upload remaining photos from the same media group (if any).
        if len(album_photos) > 1:
            extra_uploads = []
            for photo in album_photos[1:]:
                photo_bytes = await _download_telegram_file_bytes(message, photo)
                if not photo_bytes:
                    continue
                extra_uploads.append(
                    svc.upload_album_photo(result.card_id, f"{photo.file_unique_id}.jpg", photo_bytes)
                )
            if extra_uploads:
                upload_results = await asyncio.gather(*extra_uploads)
                result.attachment_count += sum(1 for ok in upload_results if ok)

        await loading_msg.delete()
        await message.reply(
            _build_create_reply(result, svc.base_url),
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)


@router.message(Command("doing"))
@inject
async def doing_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply("Usage: /doing {id}", disable_web_page_preview=True, disable_notification=True)
        return
    input_id = args.split()[0]
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.move_task(input_id, svc.doing_list_id, actor=actor)
        await loading_msg.delete()
        await _send_move_reply(message, input_id, result, "moved to IN PROGRESS", svc.base_url)
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)


@router.message(Command("done"))
@inject
async def done_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        if not args:
            done_entries = await svc.list_recent_done(limit=10)
            await loading_msg.delete()
            await _send_todo_list(message, [("DONE", done_entries)], svc.base_url)
            return
        input_id = args.split()[0]
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.move_task(input_id, svc.done_list_id, actor=actor)
        await loading_msg.delete()
        await _send_move_reply(message, input_id, result, "marked as DONE", svc.base_url)
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)


@router.message(Command("task"))
@inject
async def task_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply("Usage: /task {id}", disable_web_page_preview=True, disable_notification=True)
        return
    input_id = args.split()[0]
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        detail = await svc.get_card_detail(input_id)
    except PlankaClientError as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return
    if not detail:
        await loading_msg.delete()
        await message.reply(f"Task '{input_id}' was not found.", disable_web_page_preview=True, disable_notification=True)
        return
    await loading_msg.delete()
    await _send_card_detail(message, detail, attachment_cache)


@router.message(Command("attach"))
@inject
async def attach_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    user_record: User | None = None,
) -> None:
    if not _can_use_planka(user_record):
        await _reply_planka_access_denied(message)
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply(
            "Usage: /attach {id} (send with file or reply to a message with file)",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    payloads = await _download_attachment_payloads(message)
    if not payloads:
        await message.reply(
            "No attachment found. Send /attach {id} with a file or reply to a file message.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return

    input_id = args.split()[0]
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        first_name, first_bytes = payloads[0]
        result = await svc.attach_file(input_id, first_name, first_bytes)
        uploaded_count = 1
        if len(payloads) > 1:
            upload_results = await asyncio.gather(
                *[
                    svc.attach_file(input_id, name, data)
                    for name, data in payloads[1:]
                ],
                return_exceptions=True,
            )
            uploaded_count += sum(1 for r in upload_results if not isinstance(r, Exception))
    except (PlankaClientError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return

    await loading_msg.delete()
    await _send_attach_reply(message, input_id, result, svc.base_url, uploaded_count)


@router.callback_query(F.data.startswith("ptask:"))
@inject
async def checklist_toggle_callback(
    callback: CallbackQuery,
    svc: FromDishka[PlankaCommandService],
    user_record: User | None = None,
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    if not _can_use_planka(user_record):
        await callback.answer(
            "Only residents and members can use Planka.",
            show_alert=True,
        )
        return
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer("Invalid data.", show_alert=True)
        return
    _, task_id, new_val_str, short_id_str = parts
    is_completed = new_val_str == "1"
    actor = (callback.from_user.id, callback.from_user.username) if callback.from_user else None
    try:
        detail = await svc.toggle_checklist_item(task_id, is_completed, short_id_str)
        # Moving a checklist item to done also moves the card to "in progress"
        if is_completed and svc.doing_list_id:
            try:
                await svc.move_task(short_id_str, svc.doing_list_id, actor=actor)
                detail = await svc.get_card_detail(short_id_str)
            except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError):
                pass  # non-fatal; checklist was still toggled
    except PlankaClientError:
        await callback.answer("Planka request failed.", show_alert=True)
        return
    if not detail:
        await callback.answer("Task not found.", show_alert=True)
        return
    full_text = _build_card_detail_text(detail)
    keyboard = _build_checklist_keyboard(detail)
    try:
        await callback.message.edit_text(full_text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.warning("Failed to update checklist message: %s", exc)
    await callback.answer()


@router.message(F.photo, F.media_group_id.is_not(None))
@inject
async def album_continuation_handler(
    message: Message,
    svc: FromDishka[PlankaCommandService],
) -> None:
    """Upload photos from album messages 2+ to the card created by the /todo handler."""
    if not message.media_group_id or not message.photo:
        return
    future = svc.get_album_future(message.media_group_id)
    if future is None:
        return
    try:
        card_id = await asyncio.wait_for(asyncio.shield(future), timeout=5.0)
    except Exception:
        logger.warning(
            "album_continuation_handler: timed out waiting for card for group %s",
            message.media_group_id,
        )
        return
    photo = message.photo[-1]
    photo_bytes = await _download_photo_bytes(message, photo)
    if photo_bytes:
        await svc.upload_album_photo(card_id, f"{photo.file_unique_id}.jpg", photo_bytes)


@router.message(F.media_group_id)
async def track_media_group_messages_for_attach(
    message: Message,
    album: list[Message] | None = None,
) -> None:
    if message.media_group_id is None:
        return
    key = (message.chat.id, message.media_group_id)
    now = time.monotonic()
    group_messages = album or [message]
    async with _ATTACH_MEDIA_GROUP_LOCK:
        # Opportunistic cleanup of expired groups.
        expired = [k for k, (ts, _) in _ATTACH_MEDIA_GROUP_CACHE.items() if now - ts > _ATTACH_MEDIA_GROUP_TTL_SECONDS]
        for k in expired:
            _ATTACH_MEDIA_GROUP_CACHE.pop(k, None)

        ts, messages = _ATTACH_MEDIA_GROUP_CACHE.get(key, (now, []))
        messages.extend(group_messages)
        # Keep by message_id uniqueness and chronological order.
        uniq = {m.message_id: m for m in messages}
        ordered = [uniq[mid] for mid in sorted(uniq)]
        _ATTACH_MEDIA_GROUP_CACHE[key] = (now, ordered)


# --- Presentation helpers ---

def _can_use_planka(user_record: User | None) -> bool:
    tier = user_record.tier if user_record else UserTier.guest
    return tier in (UserTier.resident, UserTier.member)


async def _reply_planka_access_denied(message: Message) -> None:
    await message.reply(
        "Only residents and members can use task tracker.",
        disable_web_page_preview=True,
        disable_notification=True,
    )

async def _reply_planka_error(message: Message, exc: Exception) -> None:
    if isinstance(exc, PlankaAuthError):
        await message.reply(
            "Planka authentication failed. Check BOTKA_PLANKA_USERNAME_OR_EMAIL and BOTKA_PLANKA_PASSWORD.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    elif isinstance(exc, PlankaClientError):
        logger.exception("Planka request failed")
        await message.reply("Planka request failed. Please try again.", disable_web_page_preview=True, disable_notification=True)
    elif isinstance(exc, PlankaListNotConfiguredError):
        await message.reply("The target list is not configured.", disable_web_page_preview=True, disable_notification=True)
    elif isinstance(exc, PlankaCardNotFoundError):
        await message.reply(f"Task '{exc.input_id}' was not found.", disable_web_page_preview=True, disable_notification=True)


async def _send_todo_list(
    message: Message,
    sections: list[tuple[str, list[CardEntry]]],
    base_url: str,
) -> None:
    all_lines: list[str] = []
    for label, entries in sections:
        all_lines.append(f"<b>{html.escape(label)}</b>")
        if not entries:
            all_lines.append("  (empty)")
        else:
            show_assignee = label in ("IN PROGRESS", "DONE")
            for entry in entries:
                card_url = f"{base_url}/cards/{entry.card_id}"
                link = f'<a href="{html.escape(card_url)}">{html.escape(entry.name)}</a>'
                emojis = (" 🖼" if entry.has_images else "") + (" 📎" if entry.has_other_attachments else "")
                assignee_part = f" by {html.escape(entry.assignee)}" if show_assignee and entry.assignee else ""
                all_lines.append(f"  {entry.short_id} {link}{emojis}{assignee_part}")
        all_lines.append("")
    all_lines.append("<i>/task id — view description and attachments </i>")
    await _reply_chunked(message, all_lines)


async def _send_move_reply(
    message: Message,
    input_id: str,
    result: MoveTaskResult,
    done_message: str,
    base_url: str,
) -> None:
    card_url = f"{base_url}/cards/{result.card_id}"
    link = f'<a href="{html.escape(card_url)}">{html.escape(result.card_name)}</a>'
    await message.reply(f"{input_id} {link} {done_message}", parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)


async def _send_attach_reply(
    message: Message,
    input_id: str,
    result: AttachFileResult,
    base_url: str,
    uploaded_count: int,
) -> None:
    card_url = f"{base_url}/cards/{result.card_id}"
    link = f'<a href="{html.escape(card_url)}">{html.escape(result.card_name)}</a>'
    files_part = f"{uploaded_count} file{'s' if uploaded_count != 1 else ''}"
    await message.reply(
        f"{input_id} attached {files_part} to {link}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )


async def _send_card_detail(
    message: Message,
    detail: CardDetailResult,
    attachment_cache: PlankaAttachmentCacheService,
) -> None:
    full_text = _build_card_detail_text(detail)
    keyboard = _build_checklist_keyboard(detail)
    await message.reply(
        full_text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
        disable_notification=True,
    )
    await _send_attachment_groups(message, detail.attachments, attachment_cache)


def _attachment_cache_key(attachment: PlankaAttachment) -> str:
    # Planka attachment IDs are stable for unchanged files.
    return attachment.id


def _chunk_items(items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _send_attachment_groups(
    message: Message,
    attachments: list[tuple[PlankaAttachment, bytes]],
    attachment_cache: PlankaAttachmentCacheService,
) -> None:
    if not attachments:
        return

    image_items = [item for item in attachments if item[0].is_image]
    document_items = [item for item in attachments if not item[0].is_image]

    for chunk in _chunk_items(image_items, 10):
        await _send_attachment_chunk(message, chunk, attachment_cache, is_image=True)
    for chunk in _chunk_items(document_items, 10):
        await _send_attachment_chunk(message, chunk, attachment_cache, is_image=False)


async def _send_attachment_chunk(
    message: Message,
    chunk: list[tuple[PlankaAttachment, bytes]],
    attachment_cache: PlankaAttachmentCacheService,
    *,
    is_image: bool,
) -> None:
    cache_keys = [_attachment_cache_key(att) for att, _ in chunk]
    cached_ids = await asyncio.gather(*[attachment_cache.get_file_id(k) for k in cache_keys])

    def _build_media(use_cache: bool) -> list[InputMediaPhoto | InputMediaDocument]:
        media: list[InputMediaPhoto | InputMediaDocument] = []
        for (attachment, data), cached_file_id in zip(chunk, cached_ids):
            filename = attachment.name or ("image.jpg" if is_image else "attachment.bin")
            media_obj: str | BufferedInputFile
            media_obj = cached_file_id if use_cache and cached_file_id else BufferedInputFile(data, filename=filename)
            if is_image:
                media.append(InputMediaPhoto(media=media_obj))
            else:
                media.append(InputMediaDocument(media=media_obj))
        return media

    try:
        sent_messages = await message.answer_media_group(media=_build_media(use_cache=True), disable_notification=True)  # type: ignore[arg-type]
    except TelegramBadRequest:
        # A stale file_id can fail the whole group; clear cached ids and retry once with uploads.
        for key, cached_file_id in zip(cache_keys, cached_ids):
            if cached_file_id:
                await attachment_cache.clear_file_id(key)
        try:
            sent_messages = await message.answer_media_group(media=_build_media(use_cache=False), disable_notification=True)  # type: ignore[arg-type]
        except Exception:
            logger.exception("Failed to send attachment chunk (%s)", "images" if is_image else "documents")
            return

    for (attachment, _), sent_message in zip(chunk, sent_messages):
        cache_key = _attachment_cache_key(attachment)
        if is_image and sent_message.photo:
            await attachment_cache.set_file_id(cache_key, sent_message.photo[-1].file_id)
        if not is_image and sent_message.document:
            await attachment_cache.set_file_id(cache_key, sent_message.document.file_id)


def _build_card_detail_text(detail: CardDetailResult) -> str:
    parts: list[str] = [f"<b>{html.escape(detail.name)}</b>"]
    orig, meta_lines = _split_card_description(detail.description)
    if orig.strip():
        parts.append(f"\n{html.escape(_md_unescape(orig))}")
    if detail.task_lists:
        for tl in detail.task_lists:
            parts.extend(_format_task_list(tl))
    if meta_lines:
        parts.append("")
        for ln in meta_lines:
            parts.append(f"  {html.escape(_md_unescape(ln))}")
    parts.append(
        f"<i>use /doing {detail.short_id}, /done {detail.short_id} to take the task or mark done</i>"
    )
    return "\n".join(parts)


def _build_checklist_keyboard(detail: CardDetailResult) -> InlineKeyboardMarkup | None:
    all_tasks = [t for tl in detail.task_lists for t in tl.tasks]
    if not all_tasks:
        return None
    buttons = [
        [InlineKeyboardButton(
            text=("✅ " if t.is_completed else "☑ ") + t.name[:60],
            callback_data=f"ptask:{t.id}:{0 if t.is_completed else 1}:{detail.short_id}",
        )]
        for t in all_tasks
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_task_list(tl: PlankaTaskList) -> list[str]:
    if not tl.tasks:
        return []
    title = html.escape(tl.name or "Checklist")
    lines = [f"\n  <b>{title}:</b>"]
    for t in tl.tasks:
        prefix = "✅" if t.is_completed else "☑"
        lines.append(f"  {prefix} {html.escape(t.name)}")
    return lines


_DESCRIPTION_SEPARATOR = "\n\n---\n"
_MD_ESCAPE_RE = re.compile(r"\\(.)")


def _md_unescape(text: str) -> str:
    """Strip Markdown backslash escapes (e.g. backslash-underscore) added by Planka's web editor."""
    return _MD_ESCAPE_RE.sub(r"\1", text)


def _split_card_description(description: str) -> tuple[str, list[str]]:
    """Split a card description into (original_content, assignment_metadata_lines)."""
    if _DESCRIPTION_SEPARATOR in description:
        orig, meta = description.split(_DESCRIPTION_SEPARATOR, 1)
        return orig.rstrip(), [ln for ln in meta.splitlines() if ln.strip()]
    return description.rstrip(), []


def _build_create_reply(result: CreateTodoResult, base_url: str) -> str:
    parts = []
    if result.items_created:
        parts.append(f"{result.items_created} item{'s' if result.items_created != 1 else ''}")
    if result.attachment_count:
        parts.append(
            f"{result.attachment_count} attachment{'s' if result.attachment_count != 1 else ''}"
        )
    suffix = f" ({', '.join(parts)})" if parts else ""
    card_url = f"{base_url}/cards/{result.card_id}" if base_url else ""
    card_title = html.escape(result.card_name)
    card_ref = f'<a href="{html.escape(card_url)}">{card_title}</a>' if card_url else card_title
    return f"task {result.short_id} created: {card_ref}{suffix}"


def _parse_todo_args(args: str) -> tuple[str, str, list[tuple[str, list[str]]]]:
    """Parse /todo payload into (title, description, checklist_items).

        Title is always the first line.
    Any subsequent lines that start with "- " become checklist items.
    A non-bullet line ending with ':' starts a new checklist named by that line.
    Remaining non-bullet lines are treated as description.
    """
    normalized = args.strip()
    lines = normalized.split("\n")
    card_name = lines[0].strip()
    rest_lines = lines[1:]

    checklist_groups: list[tuple[str, list[str]]] = []
    current_group_name = "Checklist"
    current_group_items: list[str] = []

    def _flush_group() -> None:
        nonlocal current_group_items
        if current_group_items:
            checklist_groups.append((current_group_name, current_group_items))
            current_group_items = []

    description_lines: list[str] = []
    for line in rest_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and stripped[2:].strip():
            current_group_items.append(stripped[2:].strip())
        elif stripped.endswith(":"):
            _flush_group()
            group_name = stripped[:-1].strip()
            current_group_name = group_name if group_name else "Checklist"
        else:
            _flush_group()
            description_lines.append(line.rstrip())

    _flush_group()

    card_description = "\n".join(description_lines).strip()
    return card_name, card_description, checklist_groups


def _message_has_attachment(message: Message | None) -> bool:
    if message is None:
        return False
    return bool(
        message.document
        or message.photo
        or message.video
        or message.audio
        or message.voice
        or message.animation
        or message.video_note
    )


def _resolve_attachment_source(message: Message) -> Message | None:
    if _message_has_attachment(message):
        return message
    if _message_has_attachment(message.reply_to_message):
        return message.reply_to_message
    return None


async def _download_attachment_payloads(message: Message) -> list[tuple[str, bytes]]:
    source = _resolve_attachment_source(message)
    if source is None:
        return []

    sources: list[Message] = [source]
    if source.media_group_id:
        key = (source.chat.id, source.media_group_id)
        async with _ATTACH_MEDIA_GROUP_LOCK:
            cached = _ATTACH_MEDIA_GROUP_CACHE.get(key)
            if cached is not None:
                sources = list(cached[1])

    payloads: list[tuple[str, bytes]] = []
    for src in sources:
        payload = await _download_single_attachment_payload(message, src)
        if payload is not None:
            payloads.append(payload)

    # De-duplicate by filename while preserving order.
    unique: dict[str, tuple[str, bytes]] = {}
    for filename, data in payloads:
        if filename not in unique:
            unique[filename] = (filename, data)
    return list(unique.values())


async def _download_single_attachment_payload(
    message: Message,
    source: Message,
) -> tuple[str, bytes] | None:

    if source.document:
        filename = source.document.file_name or f"{source.document.file_unique_id}.bin"
        data = await _download_telegram_file_bytes(message, source.document)
        return (filename, data) if data else None
    if source.photo:
        photo = source.photo[-1]
        data = await _download_telegram_file_bytes(message, photo)
        return (f"{photo.file_unique_id}.jpg", data) if data else None
    if source.video:
        filename = source.video.file_name or f"{source.video.file_unique_id}.mp4"
        data = await _download_telegram_file_bytes(message, source.video)
        return (filename, data) if data else None
    if source.audio:
        filename = source.audio.file_name or f"{source.audio.file_unique_id}.mp3"
        data = await _download_telegram_file_bytes(message, source.audio)
        return (filename, data) if data else None
    if source.voice:
        data = await _download_telegram_file_bytes(message, source.voice)
        return (f"{source.voice.file_unique_id}.ogg", data) if data else None
    if source.animation:
        filename = source.animation.file_name or f"{source.animation.file_unique_id}.gif"
        data = await _download_telegram_file_bytes(message, source.animation)
        return (filename, data) if data else None
    if source.video_note:
        data = await _download_telegram_file_bytes(message, source.video_note)
        return (f"{source.video_note.file_unique_id}.mp4", data) if data else None
    return None


async def _download_photo(message: Message) -> tuple[str, bytes] | None:
    if not message.photo:
        return None
    photo = message.photo[-1]
    data = await _download_telegram_file_bytes(message, photo)
    return (f"{photo.file_unique_id}.jpg", data) if data else None


async def _download_photo_bytes(message: Message, photo: object) -> bytes | None:
    return await _download_telegram_file_bytes(message, photo)


async def _download_telegram_file_bytes(message: Message, file_obj: object) -> bytes | None:
    try:
        buf = io.BytesIO()
        await message.bot.download(file_obj, destination=buf)  # type: ignore[union-attr]
        return buf.getvalue() or None
    except Exception:
        logger.exception("Failed to download attachment")
        return None


async def _reply_chunked(message: Message, lines: list[str]) -> None:
    chunk = ""
    first = True
    for line in lines:
        safe_line = line if len(line) <= 1000 else f"{line[:997]}..."
        candidate = f"{chunk}{safe_line}\n"
        if len(candidate) > _TELEGRAM_MAX_MESSAGE_LENGTH:
            if first:
                await message.reply(chunk.rstrip(), parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)
                first = False
            else:
                await message.answer(chunk.rstrip(), parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)
            chunk = f"{safe_line}\n"
        else:
            chunk = candidate
    if chunk.strip():
        if first:
            await message.reply(chunk.rstrip(), parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)
        else:
            await message.answer(chunk.rstrip(), parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)
