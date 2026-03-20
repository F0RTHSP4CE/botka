from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botka.handlers.planka import commands
from botka.handlers.planka.commands import _build_card_detail_text, _escape_html_with_telegram_links
from botka.handlers.user_links import format_telegram_username_link, format_user_link
from botka.services.planka_client import PlankaTask, PlankaTaskList
from botka.services.planka_command_service import CardDetailResult, CreateTodoResult


def test_format_user_link_supports_username_without_telegram_id() -> None:
    assert format_user_link(username="alice_bot") == format_telegram_username_link("alice_bot")


def test_escape_html_with_telegram_links_links_usernames_without_raw_mentions() -> None:
    rendered = _escape_html_with_telegram_links("Taken by @alice_bot & <team>")

    assert "Taken by " in rendered
    assert format_telegram_username_link("@alice_bot") in rendered
    assert "&amp; &lt;team&gt;" in rendered


def test_build_card_detail_text_links_telegram_usernames() -> None:
    detail = CardDetailResult(
        short_id=42,
        name="Review with @alice_bot",
        description="Ping @bob_builder\n\n---\nTaken by: @alice_bot (20 Mar 2026 10:00 UTC)",
        task_lists=[
            PlankaTaskList(
                id="list-1",
                name="Owners @carol_dev",
                tasks=[PlankaTask(id="task-1", name="Ask @dave_ops")],
            )
        ],
        attachments=[],
    )

    rendered = _build_card_detail_text(detail)

    assert format_telegram_username_link("@alice_bot") in rendered
    assert format_telegram_username_link("@bob_builder") in rendered
    assert format_telegram_username_link("@carol_dev") in rendered
    assert format_telegram_username_link("@dave_ops") in rendered


@pytest.mark.asyncio
async def test_send_task_detail_for_input_fetches_and_sends_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    loading_msg = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        reply=AsyncMock(return_value=loading_msg),
        from_user=SimpleNamespace(id=1, username="alice_bot"),
        media_group_id=None,
    )
    detail = CardDetailResult(short_id=35, name="Task", description="", task_lists=[], attachments=[])
    svc = SimpleNamespace(
        is_configured=True,
        todo_list_id="todo-list",
        get_card_detail=AsyncMock(return_value=detail),
    )
    attachment_cache = SimpleNamespace()
    send_card_detail = AsyncMock()
    monkeypatch.setattr(commands, "_send_card_detail", send_card_detail)

    await commands._send_task_detail_for_input(
        message,
        "35",
        svc,
        attachment_cache,
    )

    svc.get_card_detail.assert_awaited_once_with("35")
    send_card_detail.assert_awaited_once_with(message, detail, attachment_cache)


@pytest.mark.asyncio
async def test_create_todo_from_text_creates_todo() -> None:
    loading_msg = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        reply=AsyncMock(return_value=loading_msg),
        from_user=SimpleNamespace(id=1, username="alice_bot"),
        media_group_id=None,
        photo=None,
    )
    svc = SimpleNamespace(
        is_configured=True,
        todo_list_id="todo-list",
        base_url="https://planka.example",
        create_todo=AsyncMock(
            return_value=CreateTodoResult(
                short_id=91,
                card_id="card-91",
                card_name="Write docs",
                items_created=0,
                attachment_count=0,
            )
        ),
        upload_album_photo=AsyncMock(),
    )

    await commands._create_todo_from_text(
        message,
        "Write docs",
        svc,
    )

    svc.create_todo.assert_awaited_once_with(
        "Write docs",
        [],
        "todo-list",
        checklist_groups=[],
        description="",
        actor=(1, "alice_bot"),
        photo_data=None,
        media_group_id=None,
    )
    assert message.reply.await_count == 2
    assert message.reply.await_args_list[-1].args[0] == "task 91 created: <a href=\"https://planka.example/cards/card-91\">Write docs</a>"


def test_parse_task_lookup_input_only_treats_single_numeric_token_as_id() -> None:
    assert commands._parse_task_lookup_input("35") == "35"
    assert commands._parse_task_lookup_input("Write docs") is None
    assert commands._parse_task_lookup_input("35 Write docs") is None