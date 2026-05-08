from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.handlers.menu import Btn, cancel_kb, send_main_menu

from botka.config import Settings
from botka.db.models import User, UserTier
from botka.handlers.user_links import format_user_link
from botka.services.meeting_service import MeetingService

router = Router(name=__name__)

_HASHTAG_RE = re.compile(r"^#(?:agenda|агенда)\b\s*", re.IGNORECASE)


def _extract_first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _is_meeting_topic(message: Message, settings: Settings) -> bool:
    return (
        settings.meeting_chat_id is not None
        and message.chat.id == settings.meeting_chat_id
        and message.message_thread_id == settings.meeting_topic_id
    )


def _build_message_link(chat_id: int, message_id: int) -> str | None:
    chat_id_str = str(chat_id)
    if not chat_id_str.startswith("-100"):
        return None
    internal_id = chat_id_str.removeprefix("-100")
    return f"https://t.me/c/{internal_id}/{message_id}"


def _notify_text(text: str, author: str, chat_id: int, message_id: int) -> str:
    link = _build_message_link(chat_id, message_id)
    if link:
        added = f'<a href="{link}">added</a>'
    else:
        added = "added"
    return f"📋 <b>{html.escape(text)}</b>\n{added} to weekly agenda by {author}"


async def _notify_meeting_topic(
    message: Message,
    settings: Settings,
    meeting_service: MeetingService,
    topic_id: int,
    text: str,
) -> None:
    if settings.meeting_chat_id is None:
        return
    if _is_meeting_topic(message, settings):
        return
    author = format_user_link(message.from_user)
    sent = await message.bot.send_message(
        chat_id=settings.meeting_chat_id,
        message_thread_id=settings.meeting_topic_id,
        text=_notify_text(text, author, message.chat.id, message.message_id),
        disable_web_page_preview=True,
    )
    await meeting_service.set_notify_message_id(topic_id, sent.message_id)


def _extract_topic_text(message: Message, command: CommandObject | None) -> str | None:
    if command is not None:
        text = _extract_first_line(command.args or "")
    elif message.text:
        first_line = _extract_first_line(message.text)
        text = _HASHTAG_RE.sub("", first_line).strip()
    else:
        return None
    return text or None


async def _list_topics(
    message: Message,
    meeting_service: MeetingService,
) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    topics = await meeting_service.get_topics_since(since)
    if not topics:
        await message.reply("No agenda topics this week.")
        return
    lines: list[str] = []
    for i, topic in enumerate(topics, 1):
        link = _build_message_link(topic.chat_id, topic.message_id)
        if link:
            index = f'<a href="{link}">{i}</a>'
        else:
            index = str(i)
        lines.append(f"{index}. {html.escape(topic.text)}")
    await message.reply(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


async def _handle_agenda_add(
    message: Message,
    settings: Settings,
    meeting_service: MeetingService,
    user_record: User | None,
    command: CommandObject | None = None,
) -> None:
    if message.from_user is None:
        return
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        await message.reply("Only residents and members can manage the agenda.")
        return
    text = _extract_topic_text(message, command)
    if not text:
        if command is not None:
            await _list_topics(message, meeting_service)
        return
    topic = await meeting_service.add_topic(
        user_record.id, message.chat.id, message.message_id, text
    )
    reply = await message.reply(
        f"Agenda topic added: <b>{html.escape(text)}</b>",
    )
    if reply:
        await meeting_service.set_reply_message_id(topic.id, reply.message_id)
    await _notify_meeting_topic(message, settings, meeting_service, topic.id, text)


async def _handle_agenda_edit(
    message: Message,
    settings: Settings,
    meeting_service: MeetingService,
    user_record: User | None,
    command: CommandObject | None = None,
) -> None:
    if message.from_user is None:
        return
    tier = user_record.tier if user_record else UserTier.guest
    if tier not in (UserTier.resident, UserTier.member):
        return
    text = _extract_topic_text(message, command)
    if not text:
        return
    topic = await meeting_service.update_topic_by_message(
        message.chat.id, message.message_id, text
    )
    if topic is None:
        return
    if topic.bot_reply_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=topic.bot_reply_message_id,
                text=f"Agenda topic updated: <b>{html.escape(text)}</b>",
            )
        except Exception:
            pass
    if topic.notify_message_id and settings.meeting_chat_id:
        author = format_user_link(message.from_user)
        try:
            await message.bot.edit_message_text(
                chat_id=settings.meeting_chat_id,
                message_id=topic.notify_message_id,
                text=_notify_text(text, author, message.chat.id, message.message_id),
            )
        except Exception:
            pass


# ── /agenda command & #agenda hashtag ────────────────────────────


@router.message(Command("agenda"), ~F.forward_origin)
@inject
async def agenda_command_handler(
    message: Message,
    command: CommandObject,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    user_record: User | None = None,
) -> None:
    await _handle_agenda_add(message, settings, meeting_service, user_record, command)


@router.edited_message(Command("agenda"))
@inject
async def agenda_command_edit_handler(
    message: Message,
    command: CommandObject,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    user_record: User | None = None,
) -> None:
    await _handle_agenda_edit(message, settings, meeting_service, user_record, command)


@router.message(F.text.regexp(_HASHTAG_RE), ~F.forward_origin)
@inject
async def agenda_hashtag_handler(
    message: Message,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    user_record: User | None = None,
) -> None:
    await _handle_agenda_add(message, settings, meeting_service, user_record)


@router.edited_message(F.text.regexp(_HASHTAG_RE))
@inject
async def agenda_hashtag_edit_handler(
    message: Message,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    user_record: User | None = None,
) -> None:
    await _handle_agenda_edit(message, settings, meeting_service, user_record)


# ── /cancel reply ────────────────────────────────────────────────


@router.message(Command("cancel"), F.reply_to_message)
@inject
async def cancel_reply_handler(
    message: Message,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    user_record: User | None = None,
) -> None:
    if message.from_user is None or message.reply_to_message is None:
        return
    replied = message.reply_to_message
    topic = await meeting_service.cancel_topic_by_reply_message(
        message.chat.id, replied.message_id
    )
    if topic is None:
        return
    if user_record is None or user_record.id != topic.user_id:
        await message.reply("Only the author can cancel this topic.")
        return
    await meeting_service.cancel_topic_by_id(topic.id)
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=replied.message_id,
            text=f"<s>{html.escape(topic.text)}</s>",
        )
    except Exception:
        pass
    if topic.notify_message_id and settings.meeting_chat_id:
        try:
            await message.bot.edit_message_text(
                chat_id=settings.meeting_chat_id,
                message_id=topic.notify_message_id,
                text=f"<s>{html.escape(topic.text)}</s>",
            )
        except Exception:
            pass


# ── Menu: /agenda dialogue ───────────────────────────────────────


class AgendaDialog(StatesGroup):
    waiting_text = State()


@router.message(F.text == Btn.AGENDA, F.chat.type == "private")
@inject
async def menu_agenda_start_message(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(AgendaDialog.waiting_text)
    await message.reply(
        "Enter a topic to add to the weekly agenda:",
        reply_markup=cancel_kb(),
    )


@router.message(AgendaDialog.waiting_text, F.text != Btn.CANCEL)
@inject
async def agenda_dialog_text_handler(
    message: Message,
    settings: FromDishka[Settings],
    meeting_service: FromDishka[MeetingService],
    state: FSMContext,
    user_record: User | None = None,
) -> None:
    await state.clear()
    if not message.text:
        return
    await _handle_agenda_add(message, settings, meeting_service, user_record)
    await send_main_menu(message)
