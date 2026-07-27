from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botka.services.planka_client import PlankaCard, PlankaCardDetail
from botka.services.planka_command_service import PlankaCommandService
from botka.services.planka_mappings_service import PlankaCardMappingService


@pytest.mark.asyncio
async def test_retake_after_abandon_appends_another_taken_event(settings) -> None:
    settings.planka_doing_list_id = "doing-list"
    settings.planka_done_list_id = "done-list"
    existing_description = (
        "Original description\n\n---\n"
        "Taken by: @alice (20 Jul 2026 10:00 UTC)\n"
        "Abandoned by @alice (20 Jul 2026 11:00 UTC)"
    )
    planka = SimpleNamespace(
        get_card=AsyncMock(
            return_value=PlankaCardDetail(
                id="card-1",
                name="Sort cables",
                description=existing_description,
                task_lists=[],
                attachments=[],
                list_id="todo-list",
            )
        ),
        move_card=AsyncMock(),
        update_card=AsyncMock(),
    )
    mappings = SimpleNamespace(resolve_card_id=AsyncMock(return_value="card-1"))
    service = PlankaCommandService(
        planka,
        mappings,
        settings,
        SimpleNamespace(),
    )

    await service.move_task(
        "7",
        "doing-list",
        actor=(42, "alice"),
    )

    description = planka.update_card.await_args.kwargs["description"]
    assert description.count("Taken by: @alice") == 2
    assert description.splitlines()[-1].startswith("Taken by: @alice (")


@pytest.mark.asyncio
async def test_list_todos_batches_mapping_queries_on_shared_session(settings) -> None:
    settings.planka_todo_list_id = "todo-list"
    settings.planka_doing_list_id = "doing-list"
    cards = {
        "todo-list": [PlankaCard(id="card-1", name="Sort cables")],
        "doing-list": [PlankaCard(id="card-2", name="Assemble rack")],
    }
    details = {
        card.id: PlankaCardDetail(
            id=card.id,
            name=card.name,
            description="",
            task_lists=[],
            attachments=[],
        )
        for section in cards.values()
        for card in section
    }
    planka = SimpleNamespace(
        get_cards=AsyncMock(side_effect=lambda list_id: cards[list_id]),
        get_card=AsyncMock(side_effect=lambda card_id: details[card_id]),
    )
    mappings = SimpleNamespace(
        get_or_create_short_ids=AsyncMock(
            return_value={"card-1": 1, "card-2": 2}
        )
    )
    service = PlankaCommandService(planka, mappings, settings, SimpleNamespace())

    sections = await service.list_todos()

    mappings.get_or_create_short_ids.assert_awaited_once_with(
        ["card-1", "card-2"]
    )
    assert [entry.short_id for entry in sections.available] == [1]
    assert [entry.short_id for entry in sections.in_progress] == [2]


@pytest.mark.asyncio
async def test_card_mapping_batch_reuses_existing_ids(session) -> None:
    mappings = PlankaCardMappingService(session)

    first = await mappings.get_or_create_short_ids(["card-1", "card-2"])
    second = await mappings.get_or_create_short_ids(["card-2", "card-1", "card-2"])

    assert first.keys() == second.keys()
    assert first == second
    assert len(set(first.values())) == 2
