"""Menu FSM dialog for creating a Planka quest."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User
from botka.handlers.menu import Btn, cancel_kb, send_main_menu
from botka.handlers.planka.commands import _create_todo_from_text
from botka.services.planka_action_dispatcher import PlankaActionDispatcher
from botka.services.planka_command_service import PlankaCommandService

router = Router(name=__name__)
router.message.filter(F.chat.type == "private")


class CreateQuestDialog(StatesGroup):
    waiting_content = State()


@router.message(F.text == Btn.CREATE_QUEST)
@inject
async def menu_create_quest_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(CreateQuestDialog.waiting_content)
    await message.reply(
        "<b>Create a quest</b>\n\n"
        "Send everything in one message. The <b>first line is always the "
        "quest title</b>.\n\n"
        "<b>Just a title:</b>\n"
        "<code>Fix the kitchen tap</code>\n\n"
        "<b>Title with details:</b>\n"
        "<code>Replace the air filter\n"
        "The spare filter is on the top shelf.</code>\n\n"
        "<b>Title with a checklist:</b>\n"
        "<code>Prepare the guest room\n"
        "Have it ready before Friday.\n\n"
        "To do:\n"
        "- Wash the sheets\n"
        "- Put out fresh towels</code>\n\n"
        "For a checklist, write a heading ending with <code>:</code>, then put "
        "<code>- </code> before every item.\n\n"
        "<b>Attachments</b>\n"
        "• Photos: attach one or more photos and put the quest text in the "
        "caption.\n"
        "• Other files: create the quest first, then reply to the confirmation "
        "message with the file.",
    )
    await message.reply(
        "Enter the quest title and details:",
        reply_markup=cancel_kb(),
    )


@router.message(CreateQuestDialog.waiting_content, F.text != Btn.CANCEL)
@inject
async def create_quest_dialog_handler(
    message: Message,
    svc: FromDishka[PlankaCommandService],
    action_dispatcher: FromDishka[PlankaActionDispatcher],
    state: FSMContext,
    album: list[Message] | None = None,
    user_record: User | None = None,
) -> None:
    content = (message.text or message.caption or "").strip()
    if not content:
        await message.reply(
            "Add the quest text as a message or photo caption.",
            reply_markup=cancel_kb(),
        )
        return
    if not svc.is_configured:
        await message.reply("Planka integration is not configured.")
        return
    if not svc.todo_list_id:
        await message.reply("The quest list is not configured.")
        return

    await state.clear()
    await _create_todo_from_text(message, content, svc, action_dispatcher, album)
    await send_main_menu(message, user_record)
