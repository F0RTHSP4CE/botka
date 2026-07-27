from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from botka.config import Settings
from botka.services.planka_album_tracker import PlankaAlbumTracker
from botka.services.planka_client import (
    PlankaAttachment,
    PlankaBoard,
    PlankaCard,
    PlankaClient,
    PlankaList,
    PlankaTaskList,
)
from botka.services.planka_mappings_service import PlankaCardMappingService

logger = logging.getLogger(__name__)

_CHECKLIST_POSITION_STEP = 65536.0
_DESCRIPTION_SEPARATOR = "\n\n---\n"


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")


def _actor_label(actor: tuple[int, str | None]) -> str:
    telegram_id, username = actor
    return f"@{username}" if username else f"tg:{telegram_id}"


def _split_description(description: str) -> tuple[str, list[str]]:
    """Return (original_content, metadata_lines) splitting on the botka separator."""
    if _DESCRIPTION_SEPARATOR in description:
        orig, meta = description.split(_DESCRIPTION_SEPARATOR, 1)
        return orig.rstrip(), [ln for ln in meta.splitlines() if ln.strip()]
    return description.rstrip(), []


def _rebuild_description(original: str, meta_lines: list[str]) -> str:
    if not meta_lines:
        return original
    return original + _DESCRIPTION_SEPARATOR + "\n".join(meta_lines)


def _append_meta_event(description: str, event_line: str) -> str:
    orig, meta_lines = _split_description(description)
    meta_lines.append(event_line)
    return _rebuild_description(orig, meta_lines)


def _extract_assignee(description: str) -> str | None:
    """Return the @username from the most recent meta line, if any."""
    _, meta_lines = _split_description(description)
    for line in reversed(meta_lines):
        for part in line.split():
            if part.startswith("@"):
                return part
    return None


class PlankaCardNotFoundError(Exception):
    def __init__(self, input_id: str) -> None:
        self.input_id = input_id
        super().__init__(f"Card '{input_id}' not found")


class PlankaListNotConfiguredError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CardEntry:
    short_id: int
    card_id: str
    name: str
    has_images: bool
    has_other_attachments: bool
    assignee: str | None = None  # e.g. "@username" extracted from description


class CardState(StrEnum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class TodoSections:
    available: tuple[CardEntry, ...]
    in_progress: tuple[CardEntry, ...]


@dataclass(frozen=True, slots=True)
class CreateTodoResult:
    short_id: int
    card_id: str
    card_name: str
    items_created: int
    attachment_count: int


@dataclass(frozen=True, slots=True)
class MoveTaskResult:
    card_id: str
    card_name: str


@dataclass(frozen=True, slots=True)
class AttachFileResult:
    card_id: str
    card_name: str
    filename: str


@dataclass(slots=True)
class CardDetailResult:
    short_id: int
    name: str
    description: str
    task_lists: list[PlankaTaskList]
    attachments: list[tuple[PlankaAttachment, bytes]] = field(default_factory=list)
    state: CardState | None = None


class PlankaCommandService:
    def __init__(
        self,
        planka: PlankaClient,
        mappings: PlankaCardMappingService,
        settings: Settings,
        tracker: PlankaAlbumTracker,
    ) -> None:
        self._planka = planka
        self._mappings = mappings
        self._settings = settings
        self._tracker = tracker

    @property
    def is_configured(self) -> bool:
        return self._planka.is_configured

    @property
    def todo_list_id(self) -> str | None:
        return self._settings.planka_todo_list_id

    @property
    def doing_list_id(self) -> str | None:
        return self._settings.planka_doing_list_id

    @property
    def done_list_id(self) -> str | None:
        return self._settings.planka_done_list_id

    @property
    def base_url(self) -> str:
        return (self._settings.planka_base_url or "").rstrip("/")

    @property
    def show_card_links(self) -> bool:
        return self._settings.planka_show_card_links

    async def list_boards(self) -> list[PlankaBoard]:
        return await self._planka.list_boards()

    async def get_board_lists(self, board_id: str) -> list[PlankaList]:
        return await self._planka.get_board_lists(board_id)

    async def list_todos(self) -> TodoSections:
        if not self.todo_list_id:
            raise PlankaListNotConfiguredError("TODO list is not configured")
        sections_cfg = [self.todo_list_id]
        if self.doing_list_id:
            sections_cfg.append(self.doing_list_id)

        card_lists = await asyncio.gather(
            *(self._planka.get_cards(list_id) for list_id in sections_cfg)
        )
        available_count = len(card_lists[0])
        entries = await self._card_entries(
            [card for cards in card_lists for card in cards]
        )
        return TodoSections(
            tuple(entries[:available_count]),
            tuple(entries[available_count:]),
        )

    async def list_recent_done(self, limit: int = 10) -> list[CardEntry]:
        if not self._settings.planka_done_list_id:
            raise PlankaListNotConfiguredError("DONE list is not configured")

        done_cards = await self._planka.get_cards(self._settings.planka_done_list_id)
        recent_cards = list(reversed(done_cards[-limit:]))

        return await self._card_entries(recent_cards)

    async def _card_entries(self, cards: list[PlankaCard]) -> list[CardEntry]:
        if not cards:
            return []
        details_results = await asyncio.gather(
            *(self._planka.get_card(card.id) for card in cards),
            return_exceptions=True,
        )
        short_ids = await self._mappings.get_or_create_short_ids(
            [card.id for card in cards]
        )
        entries = []
        for card, detail_result in zip(cards, details_results):
            detail = (
                None if isinstance(detail_result, Exception) else detail_result
            )
            entries.append(
                CardEntry(
                    short_id=short_ids[card.id],
                    card_id=card.id,
                    name=card.name,
                    has_images=bool(
                        detail and any(att.is_image for att in detail.attachments)
                    ),
                    has_other_attachments=bool(
                        detail and detail.has_other_attachments
                    ),
                    assignee=_extract_assignee(detail.description) if detail else None,
                )
            )
        return entries

    async def resolve_card_id(self, input_id: str) -> str | None:
        return await self._mappings.resolve_card_id(input_id)

    async def create_todo(
        self,
        card_name: str,
        checklist_items: list[str],
        list_id: str,
        *,
        checklist_groups: list[tuple[str, list[str]]] | None = None,
        description: str | None = None,
        actor: tuple[int, str | None] | None = None,
        photo_data: tuple[str, bytes] | None = None,
        media_group_id: str | None = None,
    ) -> CreateTodoResult:
        # Register a Future *before* any await so that concurrent continuation-message
        # tasks (photos 2, 3 … in the same album) can find the card_id as soon as it
        # is available.
        if media_group_id:
            self._tracker.create_pending(media_group_id)

        try:
            card = await self._planka.create_card(
                list_id,
                name=card_name,
                description=description,
                card_type=self._settings.planka_card_type,
            )
        except Exception:
            if media_group_id:
                self._tracker.discard(media_group_id)
            raise

        if media_group_id:
            self._tracker.set_result(media_group_id, card.id)

        short_id = await self._mappings.get_or_create_short_id(card.id)

        items_created = 0
        effective_groups = checklist_groups
        if effective_groups is None and checklist_items:
            effective_groups = [("Checklist", checklist_items)]

        if effective_groups:
            for group_name, group_items in effective_groups:
                if not group_items:
                    continue
                task_list = await self._planka.create_task_list(
                    card.id, name=group_name or "Checklist"
                )
                for idx, item_name in enumerate(group_items):
                    await self._planka.create_task(
                        task_list.id,
                        name=item_name,
                        position=_CHECKLIST_POSITION_STEP * (idx + 1),
                    )
                    items_created += 1

        attachment_count = 0
        if photo_data:
            filename, file_bytes = photo_data
            try:
                await self._planka.create_attachment(
                    card.id, file_name=filename, file_bytes=file_bytes
                )
                attachment_count = 1
            except Exception:
                logger.exception(
                    "Failed to upload photo attachment for card %s", card.id
                )

        # Annotate description and assign card member when actor is known
        if actor is not None:
            new_description = _append_meta_event(
                card.description,
                f"Created by {_actor_label(actor)} ({_now_label()})",
            )
            try:
                await self._planka.update_card(card.id, description=new_description)
            except Exception:
                logger.exception("Failed to annotate description for card %s", card.id)

        if media_group_id:
            asyncio.create_task(self._expire_pending_album(media_group_id))

        return CreateTodoResult(
            short_id=short_id,
            card_id=card.id,
            card_name=card.name,
            items_created=items_created,
            attachment_count=attachment_count,
        )

    async def _expire_pending_album(
        self, media_group_id: str, delay: float = 3.0
    ) -> None:
        await asyncio.sleep(delay)
        self._tracker.discard(media_group_id)

    def get_album_future(self, media_group_id: str) -> asyncio.Future[str] | None:
        return self._tracker.get(media_group_id)

    async def upload_album_photo(
        self, card_id: str, filename: str, photo_bytes: bytes
    ) -> bool:
        try:
            await self._planka.create_attachment(
                card_id, file_name=filename, file_bytes=photo_bytes
            )
            return True
        except Exception:
            logger.exception("Failed to upload album photo for card %s", card_id)
            return False

    async def attach_file(
        self, input_id: str, filename: str, file_bytes: bytes
    ) -> AttachFileResult:
        card_id = await self._mappings.resolve_card_id(input_id)
        if not card_id:
            raise PlankaCardNotFoundError(input_id)
        detail = await self._planka.get_card(card_id)
        card_name = detail.name if detail else input_id
        await self._planka.create_attachment(
            card_id, file_name=filename, file_bytes=file_bytes
        )
        return AttachFileResult(card_id=card_id, card_name=card_name, filename=filename)

    async def move_task(
        self,
        input_id: str,
        target_list_id: str | None,
        *,
        actor: tuple[int, str | None] | None = None,
        position_at_top: bool = False,
    ) -> MoveTaskResult:
        if not target_list_id:
            raise PlankaListNotConfiguredError("Target list is not configured")
        card_id = await self._mappings.resolve_card_id(input_id)
        if not card_id:
            raise PlankaCardNotFoundError(input_id)
        detail = await self._planka.get_card(card_id)
        card_name = detail.name if detail else input_id
        current_description = detail.description if detail else ""
        await self._planka.move_card(
            card_id, target_list_id, position=0.0 if position_at_top else None
        )

        if actor is not None:
            event = {
                self.done_list_id: "Done by",
                self.doing_list_id: "Taken by:",
            }.get(target_list_id, "Abandoned by")
            new_description = _append_meta_event(
                current_description,
                f"{event} {_actor_label(actor)} ({_now_label()})",
            )
            try:
                await self._planka.update_card(card_id, description=new_description)
            except Exception:
                logger.exception(
                    "Failed to annotate description for card %s", card_id
                )

        return MoveTaskResult(card_id=card_id, card_name=card_name)

    async def get_card_detail(self, input_id: str) -> CardDetailResult | None:
        card_id = await self._mappings.resolve_card_id(input_id)
        if not card_id:
            return None
        detail = await self._planka.get_card(card_id)
        if not detail:
            return None
        short_id = await self._mappings.get_or_create_short_id(detail.id)
        downloaded_attachments: list[tuple[PlankaAttachment, bytes]] = []
        if detail.attachments:
            download_results = await asyncio.gather(
                *[
                    self._planka.download_attachment(att.url)
                    for att in detail.attachments
                ],
                return_exceptions=True,
            )
            downloaded_attachments = [
                (att, data)
                for data, att in zip(download_results, detail.attachments)
                if isinstance(data, bytes) and data
            ]
        return CardDetailResult(
            short_id=short_id,
            name=detail.name,
            description=detail.description,
            task_lists=detail.task_lists,
            attachments=downloaded_attachments,
            state=self._card_state(detail.list_id),
        )

    def _card_state(self, list_id: str) -> CardState | None:
        return {
            self.todo_list_id: CardState.TODO,
            self.doing_list_id: CardState.DOING,
            self.done_list_id: CardState.DONE,
        }.get(list_id)

    async def toggle_checklist_item(
        self, task_id: str, is_completed: bool, card_short_id: str
    ) -> CardDetailResult | None:
        await self._planka.toggle_task(task_id, is_completed)
        return await self.get_card_detail(card_short_id)
