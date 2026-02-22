from __future__ import annotations

import asyncio
import html
import io
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.services.planka_client import PlankaAuthError, PlankaClientError, PlankaList, PlankaTaskList
from botka.services.planka_command_service import (
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
_TELEGRAM_MAX_CAPTION_LENGTH = 1024


# --- Handlers ---

@router.message(Command("boards"))
@inject
async def boards_command(message: Message, svc: FromDishka[PlankaCommandService]) -> None:
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    try:
        boards = await svc.list_boards()
    except PlankaClientError as exc:
        await _reply_planka_error(message, exc)
        return
    if not boards:
        await message.reply("No boards were found for this Planka account.", disable_web_page_preview=True, disable_notification=True)
        return
    board_lists: dict[str, list[PlankaList]] = {}
    for b in boards[:20]:
        try:
            board_lists[b.id] = await svc.get_board_lists(b.id)
        except PlankaClientError:
            board_lists[b.id] = []
    all_lines: list[str] = ["<b>Your boards:</b>"]
    for b in boards[:20]:
        all_lines.append(f"\n<b>{html.escape(b.name)}</b> (id: <code>{html.escape(b.id)}</code>)")
        lists = board_lists.get(b.id, [])
        if lists:
            for lst in lists:
                all_lines.append(f"  - {html.escape(lst.name)} (id: <code>{html.escape(lst.id)}</code>)")
        else:
            all_lines.append("  (no lists)")
    await _reply_chunked(message, all_lines)


@router.message(Command("todo"))
@inject
async def todo_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
) -> None:
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    if not svc.todo_list_id:
        await message.reply("BOTKA_PLANKA_TODO_LIST_ID is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    try:
        if not args:
            sections = await svc.list_todos()
            await _send_todo_list(message, sections, svc.base_url)
            return
        first_word = args.split()[0]
        if first_word.isdigit():
            card_id = await svc.resolve_card_id(first_word)
            if card_id:
                actor = (message.from_user.id, message.from_user.username) if message.from_user else None
                result = await svc.move_task(first_word, svc.todo_list_id, actor=actor, position_at_top=True)
                await _send_move_reply(message, first_word, result, "moved back to TODO", svc.base_url)
                return
        card_name, checklist_items = _parse_todo_args(args)
        photo_data = await _download_photo(message)
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.create_todo(
            card_name, checklist_items, svc.todo_list_id,
            actor=actor,
            photo_data=photo_data,
            media_group_id=message.media_group_id,
        )
        await message.reply(_build_create_reply(result), disable_web_page_preview=True, disable_notification=True)
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await _reply_planka_error(message, exc)


@router.message(Command("doing"))
@inject
async def doing_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
) -> None:
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply("Usage: /doing {id}", disable_web_page_preview=True, disable_notification=True)
        return
    input_id = args.split()[0]
    try:
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.move_task(input_id, svc.doing_list_id, actor=actor)
        await _send_move_reply(message, input_id, result, "moved to IN PROGRESS", svc.base_url)
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await _reply_planka_error(message, exc)


@router.message(Command("done"))
@inject
async def done_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
) -> None:
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply("Usage: /done {id}", disable_web_page_preview=True, disable_notification=True)
        return
    input_id = args.split()[0]
    try:
        actor = (message.from_user.id, message.from_user.username) if message.from_user else None
        result = await svc.move_task(input_id, svc.done_list_id, actor=actor)
        await _send_move_reply(message, input_id, result, "marked as DONE", svc.base_url)
    except (PlankaClientError, PlankaListNotConfiguredError, PlankaCardNotFoundError) as exc:
        await _reply_planka_error(message, exc)


@router.message(Command("task"))
@inject
async def task_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
) -> None:
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.", disable_web_page_preview=True, disable_notification=True)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply("Usage: /task {id}", disable_web_page_preview=True, disable_notification=True)
        return
    input_id = args.split()[0]
    try:
        detail = await svc.get_card_detail(input_id)
    except PlankaClientError as exc:
        await _reply_planka_error(message, exc)
        return
    if not detail:
        await message.reply(f"Task '{input_id}' was not found.", disable_web_page_preview=True, disable_notification=True)
        return
    await _send_card_detail(message, detail)


@router.callback_query(F.data.startswith("ptask:"))
@inject
async def checklist_toggle_callback(
    callback: CallbackQuery,
    svc: FromDishka[PlankaCommandService],
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
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


# --- Presentation helpers ---

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
    all_lines.append("<i> use /task id</i> to view description and images")
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


async def _send_card_detail(message: Message, detail: CardDetailResult) -> None:
    full_text = _build_card_detail_text(detail)
    keyboard = _build_checklist_keyboard(detail)

    # When there's an interactive checklist keyboard, always send text as a plain
    # message so the callback can always use edit_message_text. Photos go separately.
    if keyboard is not None or not detail.media_data:
        await message.reply(full_text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True, disable_notification=True)
        for data, filename in detail.media_data:
            try:
                await message.answer_photo(BufferedInputFile(data, filename=filename), disable_notification=True)
            except Exception:
                logger.exception("Failed to send photo")
        return

    # No checklist, with media — use caption when it fits
    caption_fits = len(full_text) <= _TELEGRAM_MAX_CAPTION_LENGTH
    if not caption_fits:
        await message.reply(full_text, parse_mode="HTML", disable_web_page_preview=True, disable_notification=True)

    if len(detail.media_data) == 1:
        data, filename = detail.media_data[0]
        try:
            if caption_fits:
                await message.reply_photo(BufferedInputFile(data, filename=filename), caption=full_text, parse_mode="HTML", disable_notification=True)
            else:
                await message.answer_photo(BufferedInputFile(data, filename=filename), disable_notification=True)
        except Exception:
            logger.exception("Failed to send photo")
    else:
        photos: list[InputMediaPhoto] = [
            InputMediaPhoto(
                media=BufferedInputFile(data, filename=filename),
                caption=full_text if i == 0 and caption_fits else None,
                parse_mode="HTML" if i == 0 and caption_fits else None,
            )
            for i, (data, filename) in enumerate(detail.media_data[:10])
        ]
        try:
            if caption_fits:
                await message.reply_media_group(media=photos, disable_notification=True)  # type: ignore[arg-type]
            else:
                await message.answer_media_group(media=photos, disable_notification=True)  # type: ignore[arg-type]
        except Exception:
            logger.exception("Failed to send media group")


def _build_card_detail_text(detail: CardDetailResult) -> str:
    parts: list[str] = [f"<b>{html.escape(detail.name)}</b>"]
    orig, meta_lines = _split_card_description(detail.description)
    if orig.strip():
        parts.append(f"\n{html.escape(_md_unescape(orig))}")
    if detail.task_lists:
        parts.append("\n<b>Checklist:</b>")
        for tl in detail.task_lists:
            parts.extend(_format_task_list(tl))
    if meta_lines:
        parts.append("")
        for ln in meta_lines:
            parts.append(f"  {html.escape(_md_unescape(ln))}")
    parts.append(
        f"<i>use /doing {detail.short_id}, /done {detail.short_id} to take the quest or mark done</i>"
    )
    return "\n".join(parts)


def _build_checklist_keyboard(detail: CardDetailResult) -> InlineKeyboardMarkup | None:
    all_tasks = [t for tl in detail.task_lists for t in tl.tasks]
    if not all_tasks:
        return None
    buttons = [
        [InlineKeyboardButton(
            text=("☑ " if t.is_completed else "☐ ") + t.name[:60],
            callback_data=f"ptask:{t.id}:{0 if t.is_completed else 1}:{detail.short_id}",
        )]
        for t in all_tasks
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_task_list(tl: PlankaTaskList) -> list[str]:
    if not tl.tasks:
        return []
    lines = []
    for t in tl.tasks:
        prefix = "☑" if t.is_completed else "☐"
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


def _build_create_reply(result: CreateTodoResult) -> str:
    parts = []
    if result.items_created:
        parts.append(f"{result.items_created} item{'s' if result.items_created != 1 else ''}")
    if result.has_attachment:
        parts.append("1 attachment")
    suffix = f" ({', '.join(parts)})" if parts else ""
    return f"task {result.short_id} created{suffix}"


def _parse_todo_args(args: str) -> tuple[str, list[str]]:
    lines = args.split("\n")
    card_name = lines[0].strip()
    checklist_items = [
        line.strip()[2:].strip()
        for line in lines[1:]
        if line.strip().startswith("- ") and line.strip()[2:].strip()
    ]
    return card_name, checklist_items


async def _download_photo(message: Message) -> tuple[str, bytes] | None:
    if not message.photo:
        return None
    photo = message.photo[-1]
    data = await _download_photo_bytes(message, photo)
    return (f"{photo.file_unique_id}.jpg", data) if data else None


async def _download_photo_bytes(message: Message, photo: object) -> bytes | None:
    try:
        buf = io.BytesIO()
        await message.bot.download(photo, destination=buf)  # type: ignore[union-attr]
        return buf.getvalue() or None
    except Exception:
        logger.exception("Failed to download photo")
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
