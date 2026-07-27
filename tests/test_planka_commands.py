from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import User as TelegramUser

from botka.handlers.planka import commands
from botka.handlers.planka.commands import (
    _build_card_detail_text,
    _build_checklist_keyboard,
    _edit_task_message,
    _escape_html_with_telegram_links,
    _send_card_detail_to_private,
)
from botka.handlers.user_links import format_telegram_username_link, format_user_link
from botka.services.planka_action_dispatcher import LocalActionNotification
from botka.services.planka_client import PlankaAttachment, PlankaTask, PlankaTaskList
from botka.services.planka_command_service import (
    AttachFileResult,
    CardDetailResult,
    CardEntry,
    CreateTodoResult,
    MoveTaskResult,
)
from botka.services.planka_todo_publisher import TodoPublication, TodoView


def _entry(
    short_id: int,
    name: str,
    *,
    images: bool = False,
    files: bool = False,
) -> CardEntry:
    return CardEntry(
        short_id=short_id,
        card_id=f"card-{short_id}",
        name=name,
        has_images=images,
        has_other_attachments=files,
    )


def test_format_user_link_supports_username_without_telegram_id() -> None:
    assert format_user_link(username="alice_bot") == format_telegram_username_link(
        "alice_bot"
    )


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
async def test_send_task_detail_for_input_fetches_and_sends_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loading_msg = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        reply=AsyncMock(return_value=loading_msg),
        from_user=SimpleNamespace(id=1, username="alice_bot"),
        media_group_id=None,
        bot=SimpleNamespace(),
        chat=SimpleNamespace(type="group"),
    )
    detail = CardDetailResult(
        short_id=35, name="Task", description="", task_lists=[], attachments=[]
    )
    svc = SimpleNamespace(
        is_configured=True,
        todo_list_id="todo-list",
        get_card_detail=AsyncMock(return_value=detail),
    )
    attachment_cache = SimpleNamespace()
    send_card_detail = AsyncMock(return_value=True)
    monkeypatch.setattr(
        commands, "_send_card_detail_to_private", send_card_detail
    )

    await commands._send_task_detail_for_input(
        message,
        "35",
        svc,
        attachment_cache,
    )

    svc.get_card_detail.assert_awaited_once_with("35")
    send_card_detail.assert_awaited_once_with(
        message.bot, 1, detail, attachment_cache
    )
    assert message.reply.await_args_list[-1].args[0] == (
        "📬 Quest details sent to you privately."
    )


@pytest.mark.asyncio
async def test_create_todo_from_text_creates_todo() -> None:
    user = TelegramUser(
        id=1,
        is_bot=False,
        first_name="Alice",
        username="alice_bot",
    )
    message = SimpleNamespace(
        reply=AsyncMock(),
        from_user=user,
        media_group_id=None,
        photo=None,
        bot=SimpleNamespace(),
    )
    svc = SimpleNamespace(
        is_configured=True,
        todo_list_id="todo-list",
        base_url="https://planka.example",
        show_card_links=True,
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
    dispatcher = SimpleNamespace(dispatch=Mock())

    await commands._create_todo_from_text(
        message,
        "Write docs",
        svc,
        dispatcher,
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
    message.reply.assert_awaited_once_with(
        '📜 Quest #91 created: <a href="https://planka.example/cards/card-91">Write docs</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )
    notification = dispatcher.dispatch.call_args.args[1]
    assert notification.action_type == "createCard"
    assert notification.card_id == "card-91"
    assert notification.text == (
        '📜 New quest: <a href="https://planka.example/cards/card-91">'
        "Write docs</a>"
    )


def test_parse_task_lookup_input_only_treats_single_numeric_token_as_id() -> None:
    assert commands._parse_task_lookup_input("35") == "35"
    assert commands._parse_task_lookup_input("Write docs") is None
    assert commands._parse_task_lookup_input("35 Write docs") is None


def _quest_reply_message(kind: str) -> SimpleNamespace:
    if kind == "created":
        return SimpleNamespace(
            text="📜 Quest #91 created: Write docs",
            caption=None,
            reply_markup=None,
        )
    detail = CardDetailResult(
        short_id=91,
        name="Write docs",
        description="",
        task_lists=[],
        state="todo",
    )
    return SimpleNamespace(
        text="<b>Write docs</b>",
        caption=None,
        reply_markup=_build_checklist_keyboard(detail),
    )


@pytest.mark.parametrize("reply_kind", ["created", "details"])
@pytest.mark.asyncio
async def test_attach_without_id_resolves_replied_quest_message(
    reply_kind, monkeypatch
) -> None:
    loading = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        reply_to_message=_quest_reply_message(reply_kind),
        reply=AsyncMock(side_effect=[loading, SimpleNamespace()]),
        bot=SimpleNamespace(),
    )
    svc = SimpleNamespace(
        is_configured=True,
        base_url="https://planka.example",
        show_card_links=True,
        attach_file=AsyncMock(
            return_value=AttachFileResult(
                card_id="card-91",
                card_name="Write docs",
                filename="manual.pdf",
            )
        ),
    )
    publisher = SimpleNamespace(refresh_safely=AsyncMock())
    monkeypatch.setattr(
        commands,
        "_download_attachment_payloads",
        AsyncMock(return_value=[("manual.pdf", b"contents")]),
    )

    await commands.attach_command.__dishka_orig_func__(
        message,
        SimpleNamespace(args=None),
        svc,
        publisher,
    )

    svc.attach_file.assert_awaited_once_with(
        "91", "manual.pdf", b"contents"
    )
    publisher.refresh_safely.assert_awaited_once_with(message.bot, svc)


def test_quest_completion_uses_only_green_check_emoji() -> None:
    rendered = commands._build_quest_done_text(
        MoveTaskResult(card_id="card-1", card_name="Sort cables"),
        "https://planka.example",
    )

    assert rendered == (
        '✅ Quest complete: <a href="https://planka.example/cards/card-1">'
        "Sort cables</a>"
    )


def test_quest_list_renders_all_tasks_and_attachment_emojis() -> None:
    available = [
        _entry(1, "sort <cables>"),
        _entry(2, "assemble the rack", images=True),
    ]
    in_progress = [_entry(3, "set up network", files=True)]

    view = TodoView(tuple(available), tuple(in_progress))
    text = view.text
    keyboard = view.keyboard

    assert text == (
        "📜 <b>Quests:</b>\n"
        "- sort &lt;cables&gt;\n"
        "- assemble the rack 🖼\n\n"
        "<b>In progress:</b>\n"
        "- set up network 📎\n\n"
        "Press on the quest to view &amp; take it:"
    )
    assert [row[0].text for row in keyboard.inline_keyboard] == [
        "sort <cables>",
        "assemble the rack 🖼",
        "⚔️ set up network 📎",
    ]
    assert [row[0].callback_data for row in keyboard.inline_keyboard] == [
        "pquest:view:1",
        "pquest:view:2",
        "pquest:view:3",
    ]


def test_detail_keyboard_uses_buttons_for_quest_actions() -> None:
    todo_detail = CardDetailResult(
        short_id=7,
        name="Task",
        description="",
        task_lists=[],
        state="todo",
    )
    doing_detail = CardDetailResult(
        short_id=8,
        name="Task",
        description="",
        task_lists=[],
        state="doing",
    )

    todo_keyboard = _build_checklist_keyboard(todo_detail)
    doing_keyboard = _build_checklist_keyboard(doing_detail)

    assert todo_keyboard is not None
    assert [button.text for button in todo_keyboard.inline_keyboard[0]] == [
        "⚔️ Take quest"
    ]
    assert len(todo_keyboard.inline_keyboard) == 1
    assert doing_keyboard is not None
    assert [button.text for button in doing_keyboard.inline_keyboard[0]] == [
        "🏳️ Abandon",
        "✅ Mark done",
    ]
    assert (
        _build_checklist_keyboard(
            CardDetailResult(
                short_id=9,
                name="Completed",
                description="",
                task_lists=[],
                state="done",
            )
        )
        is None
    )


def test_open_checklist_items_use_plain_gray_squares() -> None:
    detail = CardDetailResult(
        short_id=7,
        name="Task",
        description="",
        task_lists=[
            PlankaTaskList(
                id="list-1",
                name="Steps",
                tasks=[PlankaTask(id="task-1", name="Connect cable")],
            )
        ],
        state="doing",
    )

    keyboard = _build_checklist_keyboard(detail)

    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].text == "◻️ Connect cable"
    assert "◻️ Connect cable" in _build_card_detail_text(detail)


@pytest.mark.asyncio
async def test_checking_an_in_progress_task_does_not_take_it_again() -> None:
    detail = CardDetailResult(
        short_id=7,
        name="Task",
        description="",
        task_lists=[],
        state="doing",
    )
    message = SimpleNamespace(text="detail", edit_text=AsyncMock())
    callback = SimpleNamespace(
        message=message,
        data="ptask:task-1:1:7",
        from_user=SimpleNamespace(id=42, username="alice"),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )

    async def _toggle(*args, **kwargs):
        callback.answer.assert_awaited_once_with()
        return detail

    svc = SimpleNamespace(
        doing_list_id="doing-list",
        toggle_checklist_item=AsyncMock(side_effect=_toggle),
        move_task=AsyncMock(),
    )
    dispatcher = SimpleNamespace(dispatch=Mock())

    await commands.checklist_toggle_callback.__dishka_orig_func__(
        callback, svc, dispatcher
    )

    svc.move_task.assert_not_awaited()
    callback.answer.assert_awaited_once_with()
    dispatcher.dispatch.assert_called_once_with(callback.bot)


@pytest.mark.asyncio
async def test_done_action_dispatches_topic_updates_without_awaiting_them() -> None:
    user = TelegramUser(
        id=42,
        is_bot=False,
        first_name="Alice",
        username="alice",
    )
    message = SimpleNamespace(text="detail", edit_text=AsyncMock())
    callback = SimpleNamespace(
        message=message,
        data="paction:done:7",
        from_user=user,
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    result = MoveTaskResult(card_id="card-7", card_name="Connect cable")

    async def _move(*args, **kwargs):
        callback.answer.assert_awaited_once_with("Quest completed!")
        return result

    svc = SimpleNamespace(
        todo_list_id="todo-list",
        doing_list_id="doing-list",
        done_list_id="done-list",
        base_url="https://planka.example",
        show_card_links=True,
        move_task=AsyncMock(side_effect=_move),
    )
    dispatcher = SimpleNamespace(dispatch=Mock())

    await commands.quest_action_callback.__dishka_orig_func__(
        callback,
        svc,
        dispatcher,
    )

    expected = commands._build_quest_done_text(
        result,
        svc.base_url,
        user,
        svc.show_card_links,
    )
    callback.answer.assert_awaited_once_with("Quest completed!")
    dispatcher.dispatch.assert_called_once_with(
        callback.bot,
        LocalActionNotification(
            text=expected,
            action_type="moveCard",
            card_id="card-7",
        ),
    )


@pytest.mark.asyncio
async def test_quest_list_sends_only_the_complete_list_message() -> None:
    message = SimpleNamespace(
        reply=AsyncMock(),
        chat=SimpleNamespace(type="private"),
    )
    available = [_entry(1, "sort cables")]
    in_progress = [_entry(2, "set up network")]
    view = TodoView(tuple(available), tuple(in_progress))
    todo_publisher = SimpleNamespace(
        has_targets=False,
        load=AsyncMock(return_value=view),
    )

    await commands._do_quest_list(
        message,
        SimpleNamespace(is_configured=True, todo_list_id="todo-list"),
        todo_publisher,
    )

    message.reply.assert_awaited_once()
    call = message.reply.await_args
    assert call.args[0] == view.text
    assert call.kwargs["reply_markup"] == view.keyboard


@pytest.mark.asyncio
async def test_group_quest_command_links_canonical_todo_message() -> None:
    message = SimpleNamespace(
        reply=AsyncMock(),
        chat=SimpleNamespace(type="supergroup"),
        bot=SimpleNamespace(),
    )
    svc = SimpleNamespace(is_configured=True, todo_list_id="todo-list")
    view = TodoView((_entry(1, "sort cables"),), ())
    todo_publisher = SimpleNamespace(
        has_targets=True,
        load=AsyncMock(return_value=view),
        publish=AsyncMock(
            return_value=TodoPublication(1, ("https://t.me/c/123/456",))
        ),
    )

    await commands._do_quest_list(message, svc, todo_publisher)

    todo_publisher.publish.assert_awaited_once()
    message.reply.assert_awaited_once_with(
        '📌 <a href="https://t.me/c/123/456">Open the pinned todo list</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=True,
    )


@pytest.mark.asyncio
async def test_stale_take_button_without_attachments_only_opens_details() -> None:
    message = SimpleNamespace()
    callback = SimpleNamespace(
        message=message,
        data="pquest:take:1",
        from_user=SimpleNamespace(id=42, username="alice_bot"),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    svc = SimpleNamespace(
        move_task=AsyncMock(),
        get_card_detail=AsyncMock(
            return_value=CardDetailResult(
                short_id=1,
                name="sort cables",
                description="",
                task_lists=[],
                state="todo",
            )
        ),
    )
    send_detail = AsyncMock(return_value=True)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            commands, "_send_card_detail_to_private", send_detail
        )
        await commands.quest_list_callback.__dishka_orig_func__(
            callback, svc, SimpleNamespace()
        )

    svc.move_task.assert_not_awaited()
    send_detail.assert_awaited_once()
    callback.answer.assert_awaited_once_with("📬 Quest details sent privately.")


@pytest.mark.asyncio
async def test_quest_button_with_attachments_opens_details(monkeypatch) -> None:
    attachment = object()
    detail = CardDetailResult(
        short_id=7,
        name="Task with photo",
        description="Details",
        task_lists=[],
        attachments=[attachment],  # type: ignore[list-item]
        state="todo",
    )
    message = SimpleNamespace()
    callback = SimpleNamespace(
        message=message,
        data="pquest:view:7",
        from_user=SimpleNamespace(id=42, username="alice_bot"),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    svc = SimpleNamespace(get_card_detail=AsyncMock(return_value=detail))
    send_detail = AsyncMock(return_value=True)
    monkeypatch.setattr(commands, "_send_card_detail_to_private", send_detail)
    attachment_cache = SimpleNamespace()

    await commands.quest_list_callback.__dishka_orig_func__(
        callback,
        svc,
        attachment_cache,
    )

    svc.get_card_detail.assert_awaited_once_with("7")
    send_detail.assert_awaited_once_with(
        callback.bot, 42, detail, attachment_cache
    )
    callback.answer.assert_awaited_once_with("📬 Quest details sent privately.")


@pytest.mark.asyncio
async def test_card_detail_is_sent_after_separate_attachment() -> None:
    attachment = PlankaAttachment(
        id="att-1",
        name="rack.jpg",
        is_image=True,
    )
    detail = CardDetailResult(
        short_id=7,
        name="Assemble rack",
        description="Use the short screws.",
        task_lists=[],
        attachments=[(attachment, b"image")],
        state="todo",
    )
    sent_photo = SimpleNamespace(
        photo=[SimpleNamespace(file_id="telegram-photo")],
        voice=None,
        document=None,
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=sent_photo),
        send_message=AsyncMock(),
    )
    attachment_cache = SimpleNamespace(
        get_file_id=AsyncMock(return_value=None),
        set_file_id=AsyncMock(),
        clear_file_id=AsyncMock(),
    )

    await _send_card_detail_to_private(bot, 42, detail, attachment_cache)

    photo_call = bot.send_photo.await_args
    assert "caption" not in photo_call.kwargs
    assert "reply_markup" not in photo_call.kwargs
    detail_call = bot.send_message.await_args
    assert detail_call.kwargs["text"] == (
        "<b>Assemble rack</b>\n\nUse the short screws."
    )
    assert (
        detail_call.kwargs["reply_markup"].inline_keyboard[0][0].text
        == "⚔️ Take quest"
    )
    attachment_cache.set_file_id.assert_awaited_once_with(
        "att-1", "telegram-photo"
    )


@pytest.mark.asyncio
async def test_private_card_detail_targets_requesting_users_chat() -> None:
    attachment = PlankaAttachment(
        id="att-1",
        name="rack.jpg",
        is_image=True,
    )
    detail = CardDetailResult(
        short_id=7,
        name="Assemble rack",
        description="",
        task_lists=[],
        attachments=[(attachment, b"image")],
        state="todo",
    )
    sent_photo = SimpleNamespace(
        photo=[SimpleNamespace(file_id="telegram-photo")],
        voice=None,
        document=None,
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=sent_photo),
        send_message=AsyncMock(),
    )
    attachment_cache = SimpleNamespace(
        get_file_id=AsyncMock(return_value=None),
        set_file_id=AsyncMock(),
        clear_file_id=AsyncMock(),
    )

    delivered = await _send_card_detail_to_private(
        bot, 42, detail, attachment_cache
    )

    assert delivered is True
    assert bot.send_photo.await_args.kwargs["chat_id"] == 42
    assert bot.send_message.await_args.kwargs["chat_id"] == 42


@pytest.mark.asyncio
async def test_multiple_images_are_sent_as_album_before_details() -> None:
    attachments = [
        (
            PlankaAttachment(
                id=f"att-{index}",
                name=f"rack-{index}.jpg",
                is_image=True,
            ),
            f"image-{index}".encode(),
        )
        for index in (1, 2)
    ]
    detail = CardDetailResult(
        short_id=7,
        name="Assemble rack",
        description="Use the short screws.",
        task_lists=[],
        attachments=attachments,
        state="todo",
    )
    sent_media = [
        SimpleNamespace(
            photo=[SimpleNamespace(file_id=f"telegram-photo-{index}")],
            document=None,
        )
        for index in (1, 2)
    ]
    bot = SimpleNamespace(
        send_media_group=AsyncMock(return_value=sent_media),
        send_message=AsyncMock(),
    )
    attachment_cache = SimpleNamespace(
        get_file_id=AsyncMock(return_value=None),
        set_file_id=AsyncMock(),
        clear_file_id=AsyncMock(),
    )

    delivered = await _send_card_detail_to_private(
        bot, 42, detail, attachment_cache
    )

    assert delivered is True
    media = bot.send_media_group.await_args.kwargs["media"]
    assert len(media) == 2
    assert all(item.caption is None for item in media)
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_delivery_failure_is_reported_without_posting_details(
    monkeypatch,
) -> None:
    detail = CardDetailResult(
        short_id=7,
        name="Private task",
        description="Secret details",
        task_lists=[],
        state="doing",
    )
    message = SimpleNamespace()
    callback = SimpleNamespace(
        message=message,
        data="pquest:view:7",
        from_user=SimpleNamespace(id=42, username="alice_bot"),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    svc = SimpleNamespace(get_card_detail=AsyncMock(return_value=detail))
    monkeypatch.setattr(
        commands,
        "_send_card_detail_to_private",
        AsyncMock(side_effect=RuntimeError("delivery failed")),
    )

    await commands.quest_list_callback.__dishka_orig_func__(
        callback,
        svc,
        SimpleNamespace(),
    )

    callback.answer.assert_awaited_once_with(
        "I couldn't send the quest privately. Please try again.",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_media_quest_actions_update_the_attachment_caption() -> None:
    message = SimpleNamespace(
        text=None,
        edit_caption=AsyncMock(),
    )
    keyboard = _build_checklist_keyboard(
        CardDetailResult(
            short_id=7,
            name="Task",
            description="",
            task_lists=[],
            state="doing",
        )
    )

    await _edit_task_message(message, "<b>Updated task</b>", keyboard)

    message.edit_caption.assert_awaited_once_with(
        caption="<b>Updated task</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
