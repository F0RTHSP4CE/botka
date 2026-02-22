from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from botka.config import Settings
from botka.services.planka_album_tracker import PlankaAlbumTracker
from botka.services.planka_client import (
    PlankaBoard,
    PlankaClient,
    PlankaTaskList,
)
from botka.services.planka_mappings_service import PlankaCardMappingService

logger = logging.getLogger(__name__)

_CHECKLIST_POSITION_STEP = 65536.0
_DESCRIPTION_SEPARATOR = "\n\n---\n"


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")


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


@dataclass
class CardEntry:
    short_id: int
    card_id: str
    name: str
    has_images: bool
    has_other_attachments: bool
    assignee: str | None = None  # e.g. "@username" extracted from description


@dataclass
class CreateTodoResult:
    short_id: int
    items_created: int
    has_attachment: bool


@dataclass
class MoveTaskResult:
    card_id: str
    card_name: str


@dataclass
class CardDetailResult:
    short_id: int
    name: str
    description: str
    task_lists: list[PlankaTaskList]
    media_data: list[tuple[bytes, str]] = field(default_factory=list)


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

    async def list_boards(self) -> list[PlankaBoard]:
        return await self._planka.list_boards()

    async def list_todos(self) -> list[tuple[str, list[CardEntry]]]:
        sections_cfg: list[tuple[str, str]] = [("TODO", self._settings.planka_todo_list_id)]  # type: ignore[list-item]
        if self._settings.planka_doing_list_id:
            sections_cfg.append(("IN PROGRESS", self._settings.planka_doing_list_id))

        result: list[tuple[str, list[CardEntry]]] = []
        for label, list_id in sections_cfg:
            cards = await self._planka.get_cards(list_id)
            entries: list[CardEntry] = []
            for card in cards:
                short_id = await self._mappings.get_or_create_short_id(card.id)
                detail = await self._planka.get_card(card.id)
                assignee = _extract_assignee(detail.description) if detail else None
                entries.append(CardEntry(
                    short_id=short_id,
                    card_id=card.id,
                    name=card.name,
                    has_images=bool(detail and detail.attachments),
                    has_other_attachments=bool(detail and detail.has_other_attachments),
                    assignee=assignee,
                ))
            result.append((label, entries))

        if self._settings.planka_done_list_id:
            done_cards = await self._planka.get_cards(self._settings.planka_done_list_id)
            recent = done_cards[-3:]
            done_entries: list[CardEntry] = []
            for card in reversed(recent):
                short_id = await self._mappings.get_or_create_short_id(card.id)
                detail = await self._planka.get_card(card.id)
                assignee = _extract_assignee(detail.description) if detail else None
                done_entries.append(CardEntry(
                    short_id=short_id,
                    card_id=card.id,
                    name=card.name,
                    has_images=bool(detail and detail.attachments),
                    has_other_attachments=bool(detail and detail.has_other_attachments),
                    assignee=assignee,
                ))
            result.append(("DONE", done_entries))

        return result

    async def resolve_card_id(self, input_id: str) -> str | None:
        return await self._mappings.resolve_card_id(input_id)

    async def create_todo(
        self,
        card_name: str,
        checklist_items: list[str],
        list_id: str,
        *,
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
                list_id, name=card_name, card_type=self._settings.planka_card_type
            )
        except Exception:
            if media_group_id:
                self._tracker.discard(media_group_id)
            raise

        if media_group_id:
            self._tracker.set_result(media_group_id, card.id)

        short_id = await self._mappings.get_or_create_short_id(card.id)

        items_created = 0
        if checklist_items:
            task_list = await self._planka.create_task_list(card.id)
            for idx, item_name in enumerate(checklist_items):
                await self._planka.create_task(
                    task_list.id, name=item_name, position=_CHECKLIST_POSITION_STEP * (idx + 1)
                )
                items_created += 1

        has_attachment = False
        if photo_data:
            filename, file_bytes = photo_data
            try:
                await self._planka.create_attachment(card.id, file_name=filename, file_bytes=file_bytes)
                has_attachment = True
            except Exception:
                logger.exception("Failed to upload photo attachment for card %s", card.id)

        # Annotate description and assign card member when actor is known
        if actor is not None:
            telegram_id, telegram_username = actor
            actor_label = f"@{telegram_username}" if telegram_username else f"tg:{telegram_id}"
            new_description = _append_meta_event(
                card.description,
                f"Created by {actor_label} ({_now_label()})",
            )
            try:
                await self._planka.update_card(card.id, description=new_description)
            except Exception:
                logger.exception("Failed to annotate description for card %s", card.id)

        if media_group_id:
            asyncio.create_task(self._expire_pending_album(media_group_id))

        return CreateTodoResult(short_id=short_id, items_created=items_created, has_attachment=has_attachment)

    async def _expire_pending_album(self, media_group_id: str, delay: float = 3.0) -> None:
        await asyncio.sleep(delay)
        self._tracker.discard(media_group_id)

    def get_album_future(self, media_group_id: str) -> asyncio.Future[str] | None:
        return self._tracker.get(media_group_id)

    async def upload_album_photo(self, card_id: str, filename: str, photo_bytes: bytes) -> None:
        try:
            await self._planka.create_attachment(card_id, file_name=filename, file_bytes=photo_bytes)
        except Exception:
            logger.exception("Failed to upload album photo for card %s", card_id)

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
        await self._planka.move_card(card_id, target_list_id, position=0.0 if position_at_top else None)

        if actor is not None:
            telegram_id, telegram_username = actor
            actor_label = f"@{telegram_username}" if telegram_username else f"tg:{telegram_id}"

            # Determine which event label to use based on target list
            if target_list_id == self._settings.planka_done_list_id:
                event_line: str | None = f"Done by {actor_label} ({_now_label()})"
            elif target_list_id == self._settings.planka_doing_list_id:
                # Skip annotation if the task is already taken by this actor
                already_taken = _extract_assignee(current_description) == actor_label
                event_line = None if already_taken else f"Taken by: {actor_label} ({_now_label()})"
            else:
                event_line = f"Moved back by {actor_label} ({_now_label()})"

            if event_line is None:
                new_description = current_description
            else:
                new_description = _append_meta_event(current_description, event_line)
            if new_description != current_description:
                try:
                    await self._planka.update_card(card_id, description=new_description)
                except Exception:
                    logger.exception("Failed to annotate description for card %s", card_id)

        return MoveTaskResult(card_id=card_id, card_name=card_name)

    async def get_card_detail(self, input_id: str) -> CardDetailResult | None:
        card_id = await self._mappings.resolve_card_id(input_id)
        if not card_id:
            return None
        detail = await self._planka.get_card(card_id)
        if not detail:
            return None
        short_id = await self._mappings.get_or_create_short_id(detail.id)
        media_data: list[tuple[bytes, str]] = []
        for att in detail.attachments:
            data = await self._planka.download_attachment(att.url)
            if data:
                media_data.append((data, att.name or "image.jpg"))
        return CardDetailResult(
            short_id=short_id,
            name=detail.name,
            description=detail.description,
            task_lists=detail.task_lists,
            media_data=media_data,
        )

    async def toggle_checklist_item(
        self, task_id: str, is_completed: bool, card_short_id: str
    ) -> CardDetailResult | None:
        await self._planka.toggle_task(task_id, is_completed)
        return await self.get_card_detail(card_short_id)
