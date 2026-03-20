from botka.handlers.planka.commands import _build_card_detail_text, _escape_html_with_telegram_links
from botka.handlers.user_links import format_telegram_username_link, format_user_link
from botka.services.planka_client import PlankaTask, PlankaTaskList
from botka.services.planka_command_service import CardDetailResult


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