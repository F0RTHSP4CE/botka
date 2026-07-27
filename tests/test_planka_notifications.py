from botka.handlers.planka.notifications import notification_text
from botka.services.planka_client import PlankaActionEvent, PlankaListRef


def test_done_notification_uses_only_green_check_emoji() -> None:
    action = PlankaActionEvent(
        id="action-1",
        type="moveCard",
        card_id="card-1",
        card_name="Sort cables",
        user_id="user-1",
        to_list=PlankaListRef(name="Done"),
    )

    rendered = notification_text(
        action,
        "https://planka.example",
        "@alice",
    )

    assert rendered == (
        '✅ @alice completed the quest '
        '<a href="https://planka.example/cards/card-1">Sort cables</a>'
    )


def test_new_quest_notification_omits_author() -> None:
    action = PlankaActionEvent(
        id="action-2",
        type="createCard",
        card_id="card-2",
        card_name="Assemble rack",
        user_id="user-1",
        to_list=PlankaListRef(name="Quests"),
    )

    rendered = notification_text(
        action,
        "https://planka.example",
        "@alice",
    )

    assert rendered == (
        '📜 New quest: <a href="https://planka.example/cards/card-2">'
        "Assemble rack</a>"
    )
