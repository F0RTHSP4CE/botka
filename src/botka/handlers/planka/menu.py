"""Menu FSM dialog for Planka: Task lookup or creation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User
from botka.handlers.menu import Btn, cancel_kb, send_main_menu
from botka.handlers.planka.commands import _do_task_input
from botka.services.planka_attachment_cache_service import PlankaAttachmentCacheService
from botka.services.planka_command_service import PlankaCommandService

router = Router(name=__name__)
router.message.filter(F.chat.type == "private")


class TaskDialog(StatesGroup):
    waiting_text = State()


@router.message(F.text == Btn.TASK)
@inject
async def menu_task_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(TaskDialog.waiting_text)
    await message.reply(
        "Enter a quest ID to look up, or a description to create a new quest:",
        reply_markup=cancel_kb(),
    )


@router.message(TaskDialog.waiting_text, F.text != Btn.CANCEL)
@inject
async def task_dialog_text_handler(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    attachment_cache: FromDishka[PlankaAttachmentCacheService],
    state: FSMContext,
    user_record: User | None = None,
) -> None:
    await state.clear()
    if not message.text:
        return
    await _do_task_input(
        message, message.text.strip(), svc, attachment_cache, user_record
    )
    await send_main_menu(message)
