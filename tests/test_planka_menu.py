from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from botka.handlers.planka import menu


@pytest.mark.asyncio
async def test_create_quest_button_starts_dialog_with_examples_and_attachment_note() -> (
    None
):
    message = SimpleNamespace(reply=AsyncMock())
    state = SimpleNamespace(set_state=AsyncMock())

    await menu.menu_create_quest_start(message, state)

    state.set_state.assert_awaited_once_with(menu.CreateQuestDialog.waiting_content)
    assert message.reply.await_count == 2
    help_text = message.reply.await_args_list[0].args[0]
    assert "first line is always" in help_text
    assert "Fix the kitchen tap" in help_text
    assert "The spare filter is on the top shelf" in help_text
    assert "- Wash the sheets" in help_text
    assert "Photos:" in help_text
    assert "Other files:" in help_text
    assert "reply_markup" not in message.reply.await_args_list[0].kwargs

    input_prompt = message.reply.await_args_list[1]
    assert input_prompt.args[0] == "Enter the quest title and details:"
    assert input_prompt.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_create_quest_dialog_uses_photo_caption_and_album(monkeypatch) -> None:
    message = SimpleNamespace(
        text=None,
        caption="Replace air filter\nSteps:\n- Buy filter\n- Install it",
        reply=AsyncMock(),
    )
    album = [message, SimpleNamespace()]
    svc = SimpleNamespace(is_configured=True, todo_list_id="todo-list")
    dispatcher = SimpleNamespace(dispatch=Mock())
    state = SimpleNamespace(clear=AsyncMock())
    create_todo = AsyncMock()
    send_menu = AsyncMock()
    monkeypatch.setattr(menu, "_create_todo_from_text", create_todo)
    monkeypatch.setattr(menu, "send_main_menu", send_menu)
    user_record = SimpleNamespace()

    await menu.create_quest_dialog_handler.__dishka_orig_func__(
        message,
        svc,
        dispatcher,
        state,
        album,
        user_record,
    )

    state.clear.assert_awaited_once_with()
    create_todo.assert_awaited_once_with(
        message,
        message.caption,
        svc,
        dispatcher,
        album,
    )
    send_menu.assert_awaited_once_with(message, user_record)


@pytest.mark.asyncio
async def test_create_quest_dialog_keeps_waiting_without_content() -> None:
    message = SimpleNamespace(text=None, caption=None, reply=AsyncMock())
    svc = SimpleNamespace(is_configured=True, todo_list_id="todo-list")
    state = SimpleNamespace(clear=AsyncMock())

    await menu.create_quest_dialog_handler.__dishka_orig_func__(
        message,
        svc,
        SimpleNamespace(),
        state,
    )

    state.clear.assert_not_awaited()
    assert "photo caption" in message.reply.await_args.args[0]
