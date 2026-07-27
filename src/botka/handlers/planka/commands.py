from __future__ import annotations

import asyncio
import html
import io
import logging
import mimetypes
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TypeVar
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
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

from botka.handlers.menu import Btn
from botka.handlers.user_links import format_telegram_username_link, format_user_link
from botka.services.planka_action_dispatcher import (
    LocalActionNotification,
    PlankaActionDispatcher,
)
from botka.services.planka_client import (
    PlankaAttachment,
    PlankaAuthError,
    PlankaClientError,
    PlankaList,
    PlankaTaskList,
)
from botka.services.planka_attachment_cache_service import PlankaAttachmentCacheService
from botka.services.planka_command_service import (
    AttachFileResult,
    CardDetailResult,
    CardEntry,
    CardState,
    CreateTodoResult,
    MoveTaskResult,
    PlankaCardNotFoundError,
    PlankaCommandService,
    PlankaListNotConfiguredError,
)
from botka.services.planka_notification_service import (
    CREATE_CARD_ACTION,
    MOVE_CARD_ACTION,
)
from botka.services.planka_todo_publisher import (
    PlankaTodoPublisher,
    TodoPublication,
    TodoView,
)

router = Router(name=__name__)
logger = logging.getLogger(__name__)

_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_TELEGRAM_MAX_CAPTION_LENGTH = 1024
_ATTACH_MEDIA_GROUP_TTL_SECONDS = 600.0
_OPEN_CHECKBOX = "◻️"
_TASK_SHORT_ID_IN_TEXT_RE = re.compile(
    r"(?:Quest\s+#|/(?:take|doing|abandon|done)\s+)(\d+)\b",
    re.IGNORECASE,
)
_TASK_SHORT_ID_IN_CALLBACK_RE = re.compile(
    r"^(?:pquest:[^:]+|paction:[^:]+|ptask:[^:]+:[^:]+):(\d+)$"
)

_TELEGRAM_USERNAME_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{5,32})\b")

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _MoveCommandSpec:
    name: str
    target: CardState
    reply: str
    credit_actor: bool = False
    completion: bool = False


_TAKE_COMMAND = _MoveCommandSpec(
    "take", CardState.DOING, "⚔️ quest accepted!", credit_actor=True
)
_DOING_COMMAND = _MoveCommandSpec(
    "doing", CardState.DOING, "⚔️ quest accepted!", credit_actor=True
)
_ABANDON_COMMAND = _MoveCommandSpec(
    "abandon", CardState.TODO, "🏳️ quest abandoned."
)
_DONE_COMMAND = _MoveCommandSpec("done", CardState.DONE, "", completion=True)


@dataclass(frozen=True, slots=True)
class _QuestActionSpec:
    target: CardState
    answer: str
    completion: bool = False


_QUEST_ACTIONS = {
    "take": _QuestActionSpec(CardState.DOING, "⚔️ Quest accepted!"),
    "abandon": _QuestActionSpec(CardState.TODO, "🏳️ Quest abandoned."),
    "done": _QuestActionSpec(CardState.DONE, "Quest completed!", completion=True),
}


def _list_id_for_state(
    svc: PlankaCommandService, state: CardState
) -> str | None:
    return {
        CardState.TODO: svc.todo_list_id,
        CardState.DOING: svc.doing_list_id,
        CardState.DONE: svc.done_list_id,
    }[state]


def _make_card_link(
    name: str, card_id: str, base_url: str, show_links: bool = True
) -> str:
    """Return an HTML hyperlink or bold title depending on show_links."""
    escaped = html.escape(name)
    if show_links and base_url:
        card_url = f"{base_url}/cards/{card_id}"
        return f'<a href="{html.escape(card_url)}">{escaped}</a>'
    return f"<b>{escaped}</b>"


_ATTACH_MEDIA_GROUP_CACHE: dict[tuple[int, str], tuple[float, list[Message]]] = {}
_ATTACH_MEDIA_GROUP_LOCK = asyncio.Lock()


# --- Handlers ---


async def _do_boards(
    message: Message,
    svc: PlankaCommandService,
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        boards = await svc.list_boards()
        if not boards:
            await loading_msg.delete()
            await message.reply(
                "No boards were found for this Planka account.",
                disable_web_page_preview=True,
                disable_notification=True,
            )
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
                all_lines.append(
                    f"  - {html.escape(lst.name)} (id: <code>{html.escape(lst.id)}</code>)"
                )
        else:
            all_lines.append("  (no lists)")
    await loading_msg.delete()
    await _reply_chunked(message, all_lines)


async def _do_quest_list(
    message: Message,
    svc: PlankaCommandService,
    todo_publisher: PlankaTodoPublisher,
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    if not svc.todo_list_id:
        await message.reply(
            "BOTKA_PLANKA_TODO_LIST_ID is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    try:
        view = await todo_publisher.load(svc)
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await _reply_planka_error(message, exc)
        return
    if message.chat.type == "private":
        await _send_quest_list(message, view)
        return
    if not todo_publisher.has_targets:
        await message.reply(
            "Todo topics are not configured.",
            disable_notification=True,
        )
        return
    publication = await todo_publisher.publish(message.bot, view)
    await _send_todo_topic_links(message, publication)


async def _do_task_input(
    message: Message,
    text: str,
    svc: PlankaCommandService,
    attachment_cache: PlankaAttachmentCacheService,
    action_dispatcher: PlankaActionDispatcher,
) -> None:
    """Handle a task lookup or creation from plain text (used by the FSM dialog)."""
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    task_lookup_input = _parse_task_lookup_input(text)
    if task_lookup_input is not None:
        await _send_task_detail_for_input(
            message, task_lookup_input, svc, attachment_cache
        )
        return
    await _create_todo_from_text(
        message, text, svc, action_dispatcher, album=None
    )


@router.message(Command("boards"))
@inject
async def boards_command(
    message: Message,
    svc: FromDishka[PlankaCommandService],
) -> None:
    await _do_boards(message, svc)


@router.message(F.text == Btn.BOARDS, F.chat.type == "private")
@inject
async def menu_boards_message(
    message: Message,
    svc: FromDishka[PlankaCommandService],
) -> None:
    await _do_boards(message, svc)


@router.message(Command("quest"))
@inject
async def quest_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    args = (command.args or "").strip()
    if args:
        task_lookup_input = _parse_task_lookup_input(args)
        if task_lookup_input is not None:
            if not svc.is_configured:
                await message.reply(
                    "Planka integration is not configured.",
                    disable_web_page_preview=True,
                    disable_notification=True,
                )
                return
            await _send_task_detail_for_input(
                message, task_lookup_input, svc, attachment_cache
            )
            return
        await message.reply(
            "Usage: /quest [id]",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    await _do_quest_list(message, svc, todo_publisher)


@router.message(Command("todo"))
@inject
async def todo_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
    action_dispatcher: FromDishka[PlankaActionDispatcher],
    album: list[Message] | None = None,
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    if not svc.todo_list_id:
        await message.reply(
            "BOTKA_PLANKA_TODO_LIST_ID is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    args = (command.args or "").strip()
    if not args:
        await _do_quest_list(message, svc, todo_publisher)
        return
    task_lookup_input = _parse_task_lookup_input(args)
    if task_lookup_input is not None:
        await _send_task_detail_for_input(
            message, task_lookup_input, svc, attachment_cache
        )
        return
    await _create_todo_from_text(message, args, svc, action_dispatcher, album)


@router.message(F.text == Btn.TODO, F.chat.type == "private")
@inject
async def menu_todo_message(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    await _do_quest_list(message, svc, todo_publisher)


async def _run_move_command(
    message: Message,
    command: CommandObject,
    svc: PlankaCommandService,
    todo_publisher: PlankaTodoPublisher,
    spec: _MoveCommandSpec,
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return

    args = (command.args or "").strip()
    if not args:
        await message.reply(
            f"Usage: /{spec.name} {{id}}",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return

    input_id = args.split()[0]
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    actor = (
        (message.from_user.id, message.from_user.username)
        if message.from_user
        else None
    )
    try:
        result = await svc.move_task(
            input_id, _list_id_for_state(svc, spec.target), actor=actor
        )
        await todo_publisher.refresh_safely(message.bot, svc)
        await loading_msg.delete()
        if spec.completion:
            await _send_quest_done_reply(
                message,
                result,
                svc.base_url,
                from_user=message.from_user,
                show_links=svc.show_card_links,
            )
        else:
            await _send_move_reply(
                message,
                input_id,
                result,
                spec.reply,
                svc.base_url,
                from_user=message.from_user if spec.credit_actor else None,
                show_links=svc.show_card_links,
            )
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)


@router.message(Command("take"))
@inject
async def take_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    await _run_move_command(message, command, svc, todo_publisher, _TAKE_COMMAND)


@router.message(Command("abandon"))
@inject
async def abandon_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    await _run_move_command(message, command, svc, todo_publisher, _ABANDON_COMMAND)


@router.message(Command("doing"))
@inject
async def doing_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    await _run_move_command(message, command, svc, todo_publisher, _DOING_COMMAND)


@router.message(Command("done"))
@inject
async def done_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    if (command.args or "").strip():
        await _run_move_command(message, command, svc, todo_publisher, _DONE_COMMAND)
        return
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        done_entries = await svc.list_recent_done(limit=10)
        await loading_msg.delete()
        await _send_todo_list(
            message,
            [("DONE", done_entries)],
            svc.base_url,
            svc.show_card_links,
        )
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)


@router.message(Command("task"))
@inject
async def task_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
    action_dispatcher: FromDishka[PlankaActionDispatcher],
    album: list[Message] | None = None,
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply(
            "Usage: /task {id|text}",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    task_lookup_input = _parse_task_lookup_input(args)
    if task_lookup_input is not None:
        await _send_task_detail_for_input(
            message, task_lookup_input, svc, attachment_cache
        )
        return
    if not svc.todo_list_id:
        await message.reply(
            "BOTKA_PLANKA_TODO_LIST_ID is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    await _create_todo_from_text(message, args, svc, action_dispatcher, album)


@router.message(Command("attach"))
@inject
async def attach_command(
    message: Message,
    command: CommandObject,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    if not svc.is_configured:
        await message.reply(
            "Planka integration is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    args = (command.args or "").strip()
    input_id = args.split()[0] if args else None
    if input_id is None and message.reply_to_message is not None:
        input_id = _extract_task_short_id_from_message(message.reply_to_message)
    if input_id is None:
        await message.reply(
            "Send a file with /attach while replying to a quest message, "
            "or use /attach {id}.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    payloads = await _download_attachment_payloads(message)
    if not payloads:
        await message.reply(
            "No attachment found. Send a file with /attach or reply to a file message.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return

    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        result, uploaded_count = await _attach_payloads_to_task(svc, input_id, payloads)
    except (PlankaClientError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return

    await loading_msg.delete()
    await _send_attach_reply(
        message, input_id, result, svc.base_url, uploaded_count, svc.show_card_links
    )
    await todo_publisher.refresh_safely(message.bot, svc)


@router.message(
    F.reply_to_message
    & ~F.pinned_message
    & (
        F.document
        | F.photo
        | F.video
        | F.audio
        | F.voice
        | F.animation
        | F.video_note
        | F.reply_to_message.document
        | F.reply_to_message.photo
        | F.reply_to_message.video
        | F.reply_to_message.audio
        | F.reply_to_message.voice
        | F.reply_to_message.animation
        | F.reply_to_message.video_note
    )
)
@inject
async def reply_attach_to_task_handler(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
) -> None:
    if not svc.is_configured:
        return
    if message.text and message.text.startswith("/"):
        return

    payloads = await _download_attachment_payloads(message)
    if not payloads:
        return

    reply = message.reply_to_message
    if reply is None or (reply.from_user and not reply.from_user.is_bot):
        return

    input_id = _extract_task_short_id_from_message(reply)
    if not input_id:
        return

    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        result, uploaded_count = await _attach_payloads_to_task(svc, input_id, payloads)
    except (PlankaClientError, PlankaCardNotFoundError) as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return

    await loading_msg.delete()
    await _send_attach_reply(
        message, input_id, result, svc.base_url, uploaded_count, svc.show_card_links
    )
    await todo_publisher.refresh_safely(message.bot, svc)


@router.callback_query(F.data.startswith("ptask:"))
@inject
async def checklist_toggle_callback(
    callback: CallbackQuery,
    svc: FromDishka[PlankaCommandService],
    action_dispatcher: FromDishka[PlankaActionDispatcher],
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
    await callback.answer()
    actor = (
        (callback.from_user.id, callback.from_user.username)
        if callback.from_user
        else None
    )
    try:
        detail = await svc.toggle_checklist_item(task_id, is_completed, short_id_str)
        # The first completed item accepts an available quest. Further checklist
        # changes must not move it again or append duplicate assignment metadata.
        if (
            is_completed
            and detail is not None
            and detail.state == CardState.TODO
            and svc.doing_list_id
        ):
            try:
                await svc.move_task(short_id_str, svc.doing_list_id, actor=actor)
                detail = await svc.get_card_detail(short_id_str)
            except (
                PlankaClientError,
                PlankaListNotConfiguredError,
                PlankaCardNotFoundError,
            ):
                pass  # non-fatal; checklist was still toggled
    except PlankaClientError as exc:
        await _reply_planka_error(callback.message, exc)
        return
    if not detail:
        await callback.message.reply("Task not found.", disable_notification=True)
        return
    action_dispatcher.dispatch(callback.bot)
    full_text = _build_card_detail_text(detail)
    keyboard = _build_checklist_keyboard(detail)
    try:
        await _edit_task_message(callback.message, full_text, keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.warning("Failed to update checklist message: %s", exc)


@router.callback_query(F.data.startswith("pquest:"))
@inject
async def quest_list_callback(
    callback: CallbackQuery,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3 or parts[1] not in {"take", "view"}:
        await callback.answer("Invalid quest action.", show_alert=True)
        return
    _, _, short_id = parts

    try:
        detail = await svc.get_card_detail(short_id)
        if detail is None:
            await callback.answer("Quest not found.", show_alert=True)
            return
        attachments_complete = await _send_card_detail_to_private(
            callback.bot,
            callback.from_user.id,
            detail,
            attachment_cache,
        )
        if attachments_complete:
            await callback.answer("📬 Quest details sent privately.")
        else:
            await callback.answer(
                "Quest details were sent privately, but some attachments failed.",
                show_alert=True,
            )
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await _answer_planka_callback_error(callback, exc)
    except Exception as exc:
        await _answer_private_delivery_error(callback, exc)


@router.callback_query(F.data.startswith("paction:"))
@inject
async def quest_action_callback(
    callback: CallbackQuery,
    svc: FromDishka[PlankaCommandService],
    action_dispatcher: FromDishka[PlankaActionDispatcher],
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    parts = callback.data.split(":", 2)
    action_spec = _QUEST_ACTIONS.get(parts[1]) if len(parts) == 3 else None
    if action_spec is None:
        await callback.answer("Invalid quest action.", show_alert=True)
        return
    _, _, short_id = parts

    actor = (callback.from_user.id, callback.from_user.username)
    await callback.answer(action_spec.answer)

    try:
        result = await svc.move_task(
            short_id, _list_id_for_state(svc, action_spec.target), actor=actor
        )
        if action_spec.completion:
            completion_text = _build_quest_done_text(
                result,
                svc.base_url,
                callback.from_user,
                svc.show_card_links,
            )
            action_dispatcher.dispatch(
                callback.bot,
                LocalActionNotification(
                    completion_text,
                    MOVE_CARD_ACTION,
                    result.card_id,
                ),
            )
            await _edit_task_message(
                callback.message,
                completion_text,
                None,
            )
            return

        action_dispatcher.dispatch(callback.bot)
        detail = await svc.get_card_detail(short_id)
        if detail is None:
            logger.warning("Moved quest %s but could not reload its details", short_id)
            return
        await _edit_task_message(
            callback.message,
            _build_card_detail_text(detail),
            _build_checklist_keyboard(detail),
        )
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await _reply_planka_error(callback.message, exc)


@router.message(F.photo, F.media_group_id.is_not(None))
@inject
async def album_continuation_handler(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    todo_publisher: FromDishka[PlankaTodoPublisher],
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
        uploaded = await svc.upload_album_photo(
            card_id, f"{photo.file_unique_id}.jpg", photo_bytes
        )
        if uploaded:
            await todo_publisher.refresh_safely(message.bot, svc)


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
        expired = [
            k
            for k, (ts, _) in _ATTACH_MEDIA_GROUP_CACHE.items()
            if now - ts > _ATTACH_MEDIA_GROUP_TTL_SECONDS
        ]
        for k in expired:
            _ATTACH_MEDIA_GROUP_CACHE.pop(k, None)

        ts, messages = _ATTACH_MEDIA_GROUP_CACHE.get(key, (now, []))
        messages.extend(group_messages)
        # Keep by message_id uniqueness and chronological order.
        uniq = {m.message_id: m for m in messages}
        ordered = [uniq[mid] for mid in sorted(uniq)]
        _ATTACH_MEDIA_GROUP_CACHE[key] = (now, ordered)


def _parse_task_lookup_input(args: str) -> str | None:
    parts = args.split()
    if len(parts) == 1 and parts[0].isdigit():
        return parts[0]
    return None


def _actor_from_message(message: Message) -> tuple[int, str | None] | None:
    return (
        (message.from_user.id, message.from_user.username)
        if message.from_user
        else None
    )


async def _send_task_detail_for_input(
    message: Message,
    input_id: str,
    svc: PlankaCommandService,
    attachment_cache: PlankaAttachmentCacheService,
) -> None:
    loading_msg = await message.reply("⏳ Loading…", disable_notification=True)
    try:
        detail = await svc.get_card_detail(input_id)
    except PlankaClientError as exc:
        await loading_msg.delete()
        await _reply_planka_error(message, exc)
        return
    if not detail:
        await loading_msg.delete()
        await message.reply(
            f"Quest '{input_id}' was not found.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    await loading_msg.delete()
    if message.from_user is None:
        await message.reply(
            "I could not determine where to send the private message.",
            disable_notification=True,
        )
        return
    try:
        attachments_complete = await _send_card_detail_to_private(
            message.bot,
            message.from_user.id,
            detail,
            attachment_cache,
        )
    except Exception as exc:
        await _reply_private_delivery_error(message, exc)
        return

    is_private = getattr(getattr(message, "chat", None), "type", None) == "private"
    if not attachments_complete:
        await message.reply(
            "Quest details were sent privately, but some attachments could not be delivered.",
            disable_notification=True,
        )
    elif not is_private:
        await message.reply(
            "📬 Quest details sent to you privately.",
            disable_notification=True,
        )


async def _create_todo_from_text(
    message: Message,
    args: str,
    svc: PlankaCommandService,
    action_dispatcher: PlankaActionDispatcher,
    album: list[Message] | None = None,
) -> None:
    try:
        card_name, card_description, checklist_groups = _parse_todo_args(args)
        album_messages = album or [message]
        album_photos = [m.photo[-1] for m in album_messages if m.photo]
        photo_data: tuple[str, bytes] | None = None
        if album_photos:
            first_photo = album_photos[0]
            data = await _download_telegram_file_bytes(message, first_photo)
            if data:
                photo_data = (f"{first_photo.file_unique_id}.jpg", data)
        result = await svc.create_todo(
            card_name,
            [],
            svc.todo_list_id,
            checklist_groups=checklist_groups,
            description=card_description,
            actor=_actor_from_message(message),
            photo_data=photo_data,
            media_group_id=message.media_group_id,
        )

        if len(album_photos) > 1:
            extra_uploads = []
            for photo in album_photos[1:]:
                photo_bytes = await _download_telegram_file_bytes(message, photo)
                if not photo_bytes:
                    continue
                extra_uploads.append(
                    svc.upload_album_photo(
                        result.card_id, f"{photo.file_unique_id}.jpg", photo_bytes
                    )
                )
            if extra_uploads:
                upload_results = await asyncio.gather(*extra_uploads)
                result = replace(
                    result,
                    attachment_count=result.attachment_count
                    + sum(1 for ok in upload_results if ok),
                )

        await message.reply(
            _build_create_reply(result, svc.base_url, svc.show_card_links),
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        action_dispatcher.dispatch(
            message.bot,
            LocalActionNotification(
                _build_new_quest_notification(
                    result,
                    svc.base_url,
                    svc.show_card_links,
                ),
                CREATE_CARD_ACTION,
                result.card_id,
            ),
        )
    except (
        PlankaClientError,
        PlankaListNotConfiguredError,
        PlankaCardNotFoundError,
    ) as exc:
        await _reply_planka_error(message, exc)


async def _reply_planka_error(message: Message, exc: Exception) -> None:
    if isinstance(exc, PlankaAuthError):
        await message.reply(
            "Planka authentication failed. Check BOTKA_PLANKA_USERNAME_OR_EMAIL and BOTKA_PLANKA_PASSWORD.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    elif isinstance(exc, PlankaClientError):
        logger.exception("Planka request failed")
        await message.reply(
            "Planka request failed. Please try again.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    elif isinstance(exc, PlankaListNotConfiguredError):
        await message.reply(
            "The target list is not configured.",
            disable_web_page_preview=True,
            disable_notification=True,
        )
    elif isinstance(exc, PlankaCardNotFoundError):
        await message.reply(
            f"Quest '{exc.input_id}' was not found.",
            disable_web_page_preview=True,
            disable_notification=True,
        )


async def _answer_planka_callback_error(
    callback: CallbackQuery, exc: Exception
) -> None:
    if isinstance(exc, PlankaAuthError):
        text = "Planka authentication failed."
    elif isinstance(exc, PlankaCardNotFoundError):
        text = "Quest not found."
    elif isinstance(exc, PlankaListNotConfiguredError):
        text = "The target list is not configured."
    else:
        logger.exception("Planka callback request failed")
        text = "Planka request failed. Please try again."
    await callback.answer(text, show_alert=True)


def _private_delivery_error_text(exc: Exception) -> str:
    if isinstance(exc, (TelegramForbiddenError, TelegramNotFound)):
        return (
            "I can't message you privately. Open the bot's private chat, press Start, "
            "and make sure the bot is not blocked."
        )
    if isinstance(exc, TelegramBadRequest):
        return (
            "I couldn't open your private chat. Open the bot privately and press Start, "
            "then try again."
        )
    if isinstance(exc, TelegramRetryAfter):
        return "Telegram is rate-limiting messages right now. Please try again shortly."
    if isinstance(exc, TelegramNetworkError):
        return "Telegram is temporarily unreachable. Please try again."
    return "I couldn't send the quest privately. Please try again."


async def _answer_private_delivery_error(
    callback: CallbackQuery, exc: Exception
) -> None:
    if not isinstance(exc, TelegramAPIError):
        logger.exception("Unexpected private quest delivery failure")
    else:
        logger.warning("Private quest delivery failed: %s", exc)
    await callback.answer(_private_delivery_error_text(exc), show_alert=True)


async def _reply_private_delivery_error(message: Message, exc: Exception) -> None:
    if not isinstance(exc, TelegramAPIError):
        logger.exception("Unexpected private quest delivery failure")
    else:
        logger.warning("Private quest delivery failed: %s", exc)
    await message.reply(
        _private_delivery_error_text(exc),
        disable_web_page_preview=True,
        disable_notification=True,
    )


async def _send_quest_list(
    message: Message,
    view: TodoView,
) -> None:
    await message.reply(
        view.text,
        parse_mode="HTML",
        reply_markup=view.keyboard,
        disable_web_page_preview=True,
        disable_notification=True,
    )


async def _send_todo_topic_links(
    message: Message, publication: TodoPublication
) -> None:
    if publication.links:
        lines = [
            f'📌 <a href="{html.escape(link)}">Open the pinned todo list</a>'
            for link in publication.links
        ]
        await message.reply(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        return
    text = (
        "The pinned todo list was updated in the configured todo topic."
        if publication.published
        else "I couldn't update the pinned todo list. Please try again later."
    )
    await message.reply(text, disable_notification=True)


async def _send_todo_list(
    message: Message,
    sections: list[tuple[str, list[CardEntry]]],
    base_url: str,
    show_links: bool = True,
) -> None:
    all_lines: list[str] = []
    for label, entries in sections:
        all_lines.append(f"<b>{html.escape(label)}</b>")
        if not entries:
            all_lines.append("  (empty)")
        else:
            show_assignee = label in ("IN PROGRESS", "DONE")
            for entry in entries:
                link = _make_card_link(entry.name, entry.card_id, base_url, show_links)
                emojis = (" 🖼" if entry.has_images else "") + (
                    " 📎" if entry.has_other_attachments else ""
                )
                assignee_part = (
                    f" by {_escape_html_with_telegram_links(entry.assignee)}"
                    if show_assignee and entry.assignee
                    else ""
                )
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
    from_user: object = None,
    show_links: bool = True,
) -> None:
    from aiogram.types import User as _AiogramUser

    link = _make_card_link(result.card_name, result.card_id, base_url, show_links)
    by_part = (
        f" by {format_user_link(from_user)}"
        if isinstance(from_user, _AiogramUser)
        else ""
    )
    await message.reply(
        f"{input_id} {link} {done_message}{by_part}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )


async def _send_quest_done_reply(
    message: Message,
    result: MoveTaskResult,
    base_url: str,
    from_user: object = None,
    show_links: bool = True,
) -> None:
    text = _build_quest_done_text(result, base_url, from_user, show_links)
    await message.reply(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )


def _build_quest_done_text(
    result: MoveTaskResult,
    base_url: str,
    from_user: object = None,
    show_links: bool = True,
) -> str:
    link = _make_card_link(result.card_name, result.card_id, base_url, show_links)
    from aiogram.types import User as _AiogramUser

    if isinstance(from_user, _AiogramUser):
        return f"✅ {format_user_link(from_user)} completed the quest {link}"
    return f"✅ Quest complete: {link}"


async def _send_attach_reply(
    message: Message,
    input_id: str,
    result: AttachFileResult,
    base_url: str,
    uploaded_count: int,
    show_links: bool = True,
) -> None:
    link = _make_card_link(result.card_name, result.card_id, base_url, show_links)
    files_part = f"{uploaded_count} file{'s' if uploaded_count != 1 else ''}"
    await message.reply(
        f"{input_id} attached {files_part} to {link}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )


async def _attach_payloads_to_task(
    svc: PlankaCommandService,
    input_id: str,
    payloads: list[tuple[str, bytes]],
) -> tuple[AttachFileResult, int]:
    first_name, first_bytes = payloads[0]
    result = await svc.attach_file(input_id, first_name, first_bytes)
    uploaded_count = 1
    for name, data in payloads[1:]:
        try:
            await svc.attach_file(input_id, name, data)
        except Exception:
            logger.exception("Failed to attach %s to card %s", name, result.card_id)
        else:
            uploaded_count += 1
    return result, uploaded_count


def _extract_task_short_id_from_message(message: Message) -> str | None:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    match = _TASK_SHORT_ID_IN_TEXT_RE.search(text)
    if match:
        return match.group(1)

    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", ()):
        for button in row:
            callback_data = getattr(button, "callback_data", None) or ""
            match = _TASK_SHORT_ID_IN_CALLBACK_RE.fullmatch(callback_data)
            if match:
                return match.group(1)
    return None


async def _send_card_detail_to_private(
    bot: Bot,
    user_id: int,
    detail: CardDetailResult,
    attachment_cache: PlankaAttachmentCacheService,
) -> bool:
    full_text = _build_card_detail_text(detail)
    keyboard = _build_checklist_keyboard(detail)
    attachments_complete = await _send_attachment_groups(
        bot, user_id, detail.attachments, attachment_cache
    )
    await bot.send_message(
        chat_id=user_id,
        text=full_text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
        disable_notification=True,
    )
    return attachments_complete


async def _edit_task_message(
    message: Message,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    if getattr(message, "text", None) is not None:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return
    if len(text) <= _TELEGRAM_MAX_CAPTION_LENGTH:
        await message.edit_caption(
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    await message.edit_reply_markup(reply_markup=None)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
        disable_notification=True,
    )


def _attachment_cache_key(attachment: PlankaAttachment) -> str:
    # Planka attachment IDs are stable for unchanged files.
    return attachment.id


async def _send_cached_media(
    attachment_cache: PlankaAttachmentCacheService,
    cache_key: str,
    cached_file_id: str | None,
    data: bytes,
    filename: str,
    send: Callable[[str | BufferedInputFile], Awaitable[Message]],
    valid: Callable[[Message], bool],
) -> Message | None:
    async def _upload() -> Message:
        return await send(BufferedInputFile(data, filename=filename))

    try:
        sent = await send(cached_file_id) if cached_file_id else await _upload()
    except (TelegramForbiddenError, TelegramNotFound):
        raise
    except TelegramBadRequest:
        if not cached_file_id:
            logger.exception("Failed to send quest attachment: %s", filename)
            return None
        sent = None
    except Exception:
        logger.exception("Failed to send quest attachment: %s", filename)
        return None

    if sent is not None and valid(sent):
        return sent
    if not cached_file_id:
        logger.warning("Telegram rejected quest attachment format: %s", filename)
        return None

    await attachment_cache.clear_file_id(cache_key)
    try:
        sent = await _upload()
    except (TelegramForbiddenError, TelegramNotFound):
        raise
    except Exception:
        logger.exception("Failed to resend quest attachment: %s", filename)
        return None
    return sent if valid(sent) else None


def _chunk_items(items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _escape_html_with_telegram_links(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _TELEGRAM_USERNAME_RE.finditer(text):
        parts.append(html.escape(text[cursor : match.start()]))
        parts.append(format_telegram_username_link(match.group(0)))
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def _is_ogg_payload(data: bytes) -> bool:
    # OGG container files start with "OggS" magic bytes.
    return len(data) >= 4 and data[:4] == b"OggS"


async def _send_attachment_groups(
    bot: Bot,
    user_id: int,
    attachments: list[tuple[PlankaAttachment, bytes]],
    attachment_cache: PlankaAttachmentCacheService,
) -> bool:
    if not attachments:
        return True

    image_items = [item for item in attachments if item[0].is_image]
    voice_items = [
        item
        for item in attachments
        if not item[0].is_image
        and (_is_voice_attachment(item[0]) or _is_ogg_payload(item[1]))
    ]
    document_items = [
        item
        for item in attachments
        if not item[0].is_image
        and not (_is_voice_attachment(item[0]) or _is_ogg_payload(item[1]))
    ]

    complete = True
    for chunk in _chunk_items(image_items, 10):
        complete = (
            await _send_attachment_chunk(
                bot, user_id, chunk, attachment_cache, media_kind="image"
            )
            and complete
        )
    for item in voice_items:
        complete = (
            await _send_attachment_chunk(
                bot, user_id, [item], attachment_cache, media_kind="voice"
            )
            and complete
        )
    for chunk in _chunk_items(document_items, 10):
        complete = (
            await _send_attachment_chunk(
                bot, user_id, chunk, attachment_cache, media_kind="document"
            )
            and complete
        )
    return complete


def _is_voice_attachment(attachment: PlankaAttachment) -> bool:
    name = attachment.name or ""
    url = attachment.url or ""
    url_path = urlparse(url).path if url else ""

    # Planka may strip/alter display names, so inspect both name and URL.
    candidates = [name.lower(), url_path.lower()]
    if any(candidate.endswith((".ogg", ".oga", ".opus")) for candidate in candidates):
        return True

    guessed_mimes = {
        mimetypes.guess_type(name)[0],
        mimetypes.guess_type(url_path)[0],
    }
    return any(
        mime in {"audio/ogg", "application/ogg", "audio/opus"} for mime in guessed_mimes
    )


async def _send_single_attachment(
    bot: Bot,
    user_id: int,
    item: tuple[PlankaAttachment, bytes],
    attachment_cache: PlankaAttachmentCacheService,
) -> bool:
    attachment, data = item
    cache_key = _attachment_cache_key(attachment)
    cached_file_id = await attachment_cache.get_file_id(cache_key)
    filename = attachment.name or (
        "image.jpg" if attachment.is_image else "attachment.bin"
    )
    is_voice = not attachment.is_image and (
        _is_voice_attachment(attachment) or _is_ogg_payload(data)
    )

    async def _send(media: str | BufferedInputFile) -> Message:
        common = {"disable_notification": True}
        if attachment.is_image:
            return await bot.send_photo(chat_id=user_id, photo=media, **common)
        if is_voice:
            return await bot.send_voice(chat_id=user_id, voice=media, **common)
        return await bot.send_document(chat_id=user_id, document=media, **common)

    def _valid(message: Message) -> bool:
        if attachment.is_image:
            return bool(message.photo)
        if is_voice:
            return message.voice is not None
        return message.document is not None

    sent_message = await _send_cached_media(
        attachment_cache,
        cache_key,
        cached_file_id,
        data,
        filename,
        _send,
        _valid,
    )
    if sent_message is None:
        return False

    if attachment.is_image and sent_message.photo:
        await attachment_cache.set_file_id(cache_key, sent_message.photo[-1].file_id)
    elif is_voice and sent_message.voice:
        await attachment_cache.set_file_id(cache_key, sent_message.voice.file_id)
    elif not attachment.is_image and sent_message.document:
        await attachment_cache.set_file_id(cache_key, sent_message.document.file_id)
    return True


async def _send_attachment_chunk(
    bot: Bot,
    user_id: int,
    chunk: list[tuple[PlankaAttachment, bytes]],
    attachment_cache: PlankaAttachmentCacheService,
    *,
    media_kind: str,
) -> bool:
    if not chunk:
        return True
    if len(chunk) == 1:
        return await _send_single_attachment(
            bot, user_id, chunk[0], attachment_cache
        )

    cache_keys = [_attachment_cache_key(att) for att, _ in chunk]
    cached_ids = [await attachment_cache.get_file_id(k) for k in cache_keys]

    def _build_media(use_cache: bool) -> list[InputMediaPhoto | InputMediaDocument]:
        media: list[InputMediaPhoto | InputMediaDocument] = []
        for (attachment, data), cached_file_id in zip(chunk, cached_ids):
            filename = attachment.name or (
                "image.jpg" if media_kind == "image" else "attachment.bin"
            )
            media_obj: str | BufferedInputFile
            media_obj = (
                cached_file_id
                if use_cache and cached_file_id
                else BufferedInputFile(data, filename=filename)
            )
            if media_kind == "image":
                media.append(InputMediaPhoto(media=media_obj))
            else:
                media.append(InputMediaDocument(media=media_obj))
        return media

    try:
        sent_messages = await bot.send_media_group(
            chat_id=user_id,
            media=_build_media(use_cache=True),
            disable_notification=True,
        )
    except (TelegramForbiddenError, TelegramNotFound):
        raise
    except TelegramBadRequest:
        # A stale file_id can fail the whole group; clear cached ids and retry once with uploads.
        for key, cached_file_id in zip(cache_keys, cached_ids):
            if cached_file_id:
                await attachment_cache.clear_file_id(key)
        try:
            sent_messages = await bot.send_media_group(
                chat_id=user_id,
                media=_build_media(use_cache=False),
                disable_notification=True,
            )
        except (TelegramForbiddenError, TelegramNotFound):
            raise
        except Exception:
            logger.exception("Failed to send attachment chunk (%s)", media_kind)
            return False

    for (attachment, _), sent_message in zip(chunk, sent_messages):
        cache_key = _attachment_cache_key(attachment)
        if media_kind == "image" and sent_message.photo:
            await attachment_cache.set_file_id(
                cache_key, sent_message.photo[-1].file_id
            )
        if media_kind == "document" and sent_message.document:
            await attachment_cache.set_file_id(cache_key, sent_message.document.file_id)
    return True


def _build_card_detail_text(detail: CardDetailResult) -> str:
    parts: list[str] = [f"<b>{_escape_html_with_telegram_links(detail.name)}</b>"]
    orig, meta_lines = _split_card_description(detail.description)
    if orig.strip():
        parts.append(f"\n{_escape_html_with_telegram_links(_md_unescape(orig))}")
    if detail.task_lists:
        for tl in detail.task_lists:
            parts.extend(_format_task_list(tl))
    if meta_lines:
        parts.append("")
        for ln in meta_lines:
            parts.append(f"  {_escape_html_with_telegram_links(_md_unescape(ln))}")
    return "\n".join(parts)


def _build_checklist_keyboard(detail: CardDetailResult) -> InlineKeyboardMarkup | None:
    all_tasks = [t for tl in detail.task_lists for t in tl.tasks]
    buttons = [
        [
            InlineKeyboardButton(
                text=("✅ " if t.is_completed else f"{_OPEN_CHECKBOX} ")
                + t.name[:60],
                callback_data=f"ptask:{t.id}:{0 if t.is_completed else 1}:{detail.short_id}",
            )
        ]
        for t in all_tasks
    ]
    if detail.state == CardState.TODO:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚔️ Take quest",
                    callback_data=f"paction:take:{detail.short_id}",
                )
            ]
        )
    elif detail.state == CardState.DOING:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🏳️ Abandon",
                    callback_data=f"paction:abandon:{detail.short_id}",
                ),
                InlineKeyboardButton(
                    text="✅ Mark done",
                    callback_data=f"paction:done:{detail.short_id}",
                ),
            ]
        )
    elif detail.state is None:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚔️ Take",
                    callback_data=f"paction:take:{detail.short_id}",
                ),
                InlineKeyboardButton(
                    text="🏳️ Abandon",
                    callback_data=f"paction:abandon:{detail.short_id}",
                ),
                InlineKeyboardButton(
                    text="✅ Mark done",
                    callback_data=f"paction:done:{detail.short_id}",
                ),
            ]
        )
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_task_list(tl: PlankaTaskList) -> list[str]:
    if not tl.tasks:
        return []
    title = _escape_html_with_telegram_links(tl.name or "Checklist")
    lines = [f"\n  <b>{title}:</b>"]
    for t in tl.tasks:
        prefix = "✅" if t.is_completed else _OPEN_CHECKBOX
        lines.append(f"  {prefix} {_escape_html_with_telegram_links(t.name)}")
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


def _build_create_reply(
    result: CreateTodoResult, base_url: str, show_links: bool = True
) -> str:
    parts = []
    if result.items_created:
        parts.append(
            f"{result.items_created} item{'s' if result.items_created != 1 else ''}"
        )
    if result.attachment_count:
        parts.append(
            f"{result.attachment_count} attachment{'s' if result.attachment_count != 1 else ''}"
        )
    suffix = f" ({', '.join(parts)})" if parts else ""
    card_ref = _make_card_link(result.card_name, result.card_id, base_url, show_links)
    return f"📜 Quest #{result.short_id} created: {card_ref}{suffix}"


def _build_new_quest_notification(
    result: CreateTodoResult,
    base_url: str,
    show_links: bool = True,
) -> str:
    link = _make_card_link(result.card_name, result.card_id, base_url, show_links)
    return f"📜 New quest: {link}"


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


def _build_voice_memo_filename(file_unique_id: str) -> str:
    return f"voice_memo_{file_unique_id}.ogg"


def _is_voice_like_document(message: Message) -> bool:
    document = message.document
    if document is None:
        return False
    filename = (document.file_name or "").lower()
    mime_type = (document.mime_type or "").lower()
    return filename.endswith((".ogg", ".oga", ".opus")) or mime_type in {
        "audio/ogg",
        "application/ogg",
        "audio/opus",
    }


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
        if _is_voice_like_document(source):
            filename = _build_voice_memo_filename(source.document.file_unique_id)
        else:
            filename = (
                source.document.file_name or f"{source.document.file_unique_id}.bin"
            )
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
        filename = _build_voice_memo_filename(source.voice.file_unique_id)
        return (filename, data) if data else None
    if source.animation:
        filename = (
            source.animation.file_name or f"{source.animation.file_unique_id}.gif"
        )
        data = await _download_telegram_file_bytes(message, source.animation)
        return (filename, data) if data else None
    if source.video_note:
        data = await _download_telegram_file_bytes(message, source.video_note)
        return (f"{source.video_note.file_unique_id}.mp4", data) if data else None
    return None


async def _download_photo_bytes(message: Message, photo: object) -> bytes | None:
    return await _download_telegram_file_bytes(message, photo)


async def _download_telegram_file_bytes(
    message: Message, file_obj: object
) -> bytes | None:
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
                await message.reply(
                    chunk.rstrip(),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    disable_notification=True,
                )
                first = False
            else:
                await message.answer(
                    chunk.rstrip(),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    disable_notification=True,
                )
            chunk = f"{safe_line}\n"
        else:
            chunk = candidate
    if chunk.strip():
        if first:
            await message.reply(
                chunk.rstrip(),
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=True,
            )
        else:
            await message.answer(
                chunk.rstrip(),
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=True,
            )
