from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class PlankaClientError(Exception):
    """Base exception raised for Planka API related failures."""


class PlankaAuthError(PlankaClientError):
    """Raised when Planka rejects authentication."""


@dataclass
class PlankaBoard:
    id: str
    name: str


@dataclass
class PlankaCard:
    id: str
    name: str
    description: str = ""


@dataclass
class PlankaTask:
    id: str
    name: str
    is_completed: bool = False


@dataclass
class PlankaTaskList:
    id: str
    name: str
    tasks: list[PlankaTask] = field(default_factory=list)


@dataclass
class PlankaAttachment:
    id: str
    name: str
    url: str = ""
    is_image: bool = False


@dataclass
class PlankaCardDetail:
    id: str
    name: str
    description: str
    task_lists: list[PlankaTaskList]
    attachments: list[PlankaAttachment]
    has_other_attachments: bool = False


@dataclass
class PlankaList:
    id: str
    name: str


@dataclass
class PlankaUser:
    id: str
    name: str
    username: str = ""


@dataclass
class PlankaListRef:
    name: str
    type: str = ""


@dataclass
class PlankaActionEvent:
    id: str
    type: str
    card_id: str
    card_name: str
    user_id: str | None
    to_list: PlankaListRef | None = None
    from_list: PlankaListRef | None = None


@dataclass
class PlankaActionsPage:
    actions: list[PlankaActionEvent]
    users: list[PlankaUser]


_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


class PlankaClient:
    def __init__(
        self,
        base_url: str,
        username_or_email: str,
        password: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username_or_email = username_or_email
        self._password = password
        self._timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._username_or_email and self._password)

    @property
    def is_ready(self) -> bool:
        """True only after start() has completed successfully."""
        return self._client is not None

    async def start(self) -> None:
        token = await self._login()
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(self._timeout_seconds),
        )

    async def _login(self) -> str:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_seconds)) as client:
            response = await client.post(
                f"{self._base_url}/api/access-tokens",
                json={"emailOrUsername": self._username_or_email, "password": self._password},
            )
        if response.status_code == 401:
            raise PlankaAuthError("Planka login failed: invalid credentials")
        if response.status_code == 403:
            try:
                detail = response.json().get("message") or response.text[:200]
            except ValueError:
                detail = response.text[:200]
            raise PlankaAuthError(f"Planka login forbidden: {detail}")
        if response.is_error:
            raise PlankaClientError(
                f"Planka login failed: {response.status_code} {response.text[:200]}"
            )
        token = response.json().get("item")
        if not token:
            raise PlankaAuthError("Planka login failed: no token returned")
        return token

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> dict[str, Any]:
        return await self._get_json("/api/users/me")

    async def list_boards(self) -> list[PlankaBoard]:
        def _parse(raw: list[Any]) -> list[PlankaBoard]:
            return [
                PlankaBoard(id=str(b["id"]), name=str(b.get("name", "")))
                for b in raw
                if isinstance(b, dict) and b.get("id")
            ]

        try:
            data = await self._get_json("/api/boards")
            raw = data.get("items") if isinstance(data, dict) else data
            if isinstance(raw, list) and raw:
                return _parse(raw)
        except PlankaClientError:
            pass

        data = await self._get_json("/api/projects")
        boards = (data.get("included") or {}).get("boards") or [] if isinstance(data, dict) else []
        return _parse(boards)

    async def create_card(
        self,
        list_id: str,
        name: str,
        description: str | None = None,
        card_type: str = "task",
    ) -> PlankaCard:
        payload: dict[str, Any] = {"name": name, "type": card_type, "position": 0.0}
        if description:
            payload["description"] = description
        data = await self._post_json(f"/api/lists/{list_id}/cards", payload=payload)
        raw = data.get("item") if isinstance(data, dict) else data
        if not isinstance(raw, dict) or not raw.get("id"):
            raise PlankaClientError("Planka returned a card without an ID")
        return PlankaCard(
            id=str(raw["id"]),
            name=str(raw.get("name") or name),
            description=str(raw.get("description") or ""),
        )

    async def get_cards(self, list_id: str) -> list[PlankaCard]:
        data = await self._get_json(f"/api/lists/{list_id}/cards")
        raw = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        return [
            PlankaCard(id=str(c["id"]), name=str(c.get("name") or "Untitled"))
            for c in raw
            if isinstance(c, dict) and c.get("id")
        ]

    async def get_card(self, card_id: str) -> PlankaCardDetail | None:
        data = await self._get_json(f"/api/cards/{card_id}")
        if not isinstance(data, dict):
            return None
        raw_card = data.get("item") or {}
        if not isinstance(raw_card, dict) or not raw_card.get("id"):
            return None
        included = data.get("included") or {}

        tasks_by_list: dict[str, list[PlankaTask]] = {}
        for t in included.get("tasks") or []:
            if not isinstance(t, dict) or not t.get("taskListId"):
                continue
            tasks_by_list.setdefault(str(t["taskListId"]), []).append(
                PlankaTask(
                    id=str(t.get("id", "")),
                    name=str(t.get("name") or ""),
                    is_completed=bool(t.get("isCompleted", False)),
                )
            )

        task_lists = [
            PlankaTaskList(
                id=str(tl["id"]),
                name=str(tl.get("name") or "Checklist"),
                tasks=tasks_by_list.get(str(tl["id"]), []),
            )
            for tl in (included.get("taskLists") or [])
            if isinstance(tl, dict) and tl.get("id")
        ]

        all_raw_attachments = included.get("attachments") or []
        attachments = [
            PlankaAttachment(
                id=str(att["id"]),
                name=str(att.get("name") or ""),
                url=str((att.get("data") or {}).get("url") or ""),
                is_image=any(
                    str(att.get("name") or "").lower().endswith(ext)
                    for ext in _IMAGE_EXTENSIONS
                ),
            )
            for att in all_raw_attachments
            if isinstance(att, dict) and att.get("id")
        ]
        has_other_attachments = any(
            True
            for att in attachments
            if not att.is_image
        )

        return PlankaCardDetail(
            id=str(raw_card["id"]),
            name=str(raw_card.get("name") or "Untitled"),
            description=str(raw_card.get("description") or ""),
            task_lists=task_lists,
            attachments=attachments,
            has_other_attachments=has_other_attachments,
        )

    async def download_attachment(self, attachment_url: str) -> bytes | None:
        """Download a file attachment. Uses cookie auth as Planka's /attachments/* route
        only accepts the accessToken cookie, not Bearer tokens."""
        if not self._token or not attachment_url:
            return None
        # Re-host using our base_url in case Planka's configured BASE_URL differs
        path = urlparse(attachment_url).path
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_seconds)) as client:
                response = await client.get(url, cookies={"accessToken": self._token})
            return None if response.is_error else response.content
        except httpx.HTTPError:
            return None

    async def move_card(self, card_id: str, list_id: str, *, position: float | None = None) -> None:
        await self._patch_json(
            f"/api/cards/{card_id}",
            payload={"listId": list_id, "position": position if position is not None else 65535.0},
        )

    async def create_task_list(self, card_id: str, name: str = "Checklist") -> PlankaTaskList:
        data = await self._post_json(
            f"/api/cards/{card_id}/task-lists",
            payload={"name": name, "position": 65536.0, "showOnFrontOfCard": True},
        )
        raw = data.get("item") if isinstance(data, dict) else data
        if not isinstance(raw, dict) or not raw.get("id"):
            raise PlankaClientError("Planka returned a task list without an ID")
        return PlankaTaskList(id=str(raw["id"]), name=str(raw.get("name") or name))

    async def create_task(self, task_list_id: str, name: str, position: float) -> None:
        await self._post_json(
            f"/api/task-lists/{task_list_id}/tasks",
            payload={"name": name, "position": position},
        )

    async def toggle_task(self, task_id: str, is_completed: bool) -> None:
        await self._patch_json(
            f"/api/tasks/{task_id}",
            payload={"isCompleted": is_completed},
        )

    async def update_card(self, card_id: str, *, description: str) -> None:
        """Update the description of a card."""
        await self._patch_json(f"/api/cards/{card_id}", payload={"description": description})

    async def get_board_lists(self, board_id: str) -> list[PlankaList]:
        data = await self._get_json(f"/api/boards/{board_id}")
        if not isinstance(data, dict):
            return []
        raw = (data.get("included") or {}).get("lists") or []
        return [
            PlankaList(id=str(lst["id"]), name=str(lst.get("name") or ""))
            for lst in raw
            if isinstance(lst, dict) and lst.get("id")
        ]

    async def get_board_actions(self, board_id: str) -> PlankaActionsPage:
        data = await self._get_json(f"/api/boards/{board_id}/actions")
        if not isinstance(data, dict):
            return PlankaActionsPage(actions=[], users=[])

        users = [
            PlankaUser(
                id=str(u["id"]),
                name=str(u.get("name") or u.get("username") or "Unknown"),
                username=str(u.get("username") or ""),
            )
            for u in ((data.get("included") or {}).get("users") or [])
            if isinstance(u, dict) and u.get("id")
        ]

        actions: list[PlankaActionEvent] = []
        for a in data.get("items") or []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            action_data = a.get("data") or {}
            card = action_data.get("card") or {}
            to_list_raw = action_data.get("toList") or action_data.get("list") or {}
            from_list_raw = action_data.get("fromList") or {}
            actions.append(
                PlankaActionEvent(
                    id=str(a["id"]),
                    type=str(a.get("type", "")),
                    card_id=str(a.get("cardId", "")),
                    card_name=str(card.get("name") or "Untitled"),
                    user_id=str(a["userId"]) if a.get("userId") else None,
                    to_list=PlankaListRef(
                        name=str(to_list_raw.get("name") or "?"),
                        type=str(to_list_raw.get("type") or ""),
                    )
                    if to_list_raw
                    else None,
                    from_list=PlankaListRef(name=str(from_list_raw.get("name") or "?"))
                    if from_list_raw
                    else None,
                )
            )

        return PlankaActionsPage(actions=actions, users=users)

    async def create_attachment(
        self,
        card_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str | None = None,
    ) -> None:
        resolved_content_type = content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        await self._post_multipart(
            f"/api/cards/{card_id}/attachments",
            data={"type": "file", "name": file_name},
            files={"file": (file_name, file_bytes, resolved_content_type)},
        )

    async def _get_json(self, path: str) -> Any:
        return await self._request_json("GET", path)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request_json("POST", path, payload=payload)

    async def _patch_json(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request_json("PATCH", path, payload=payload)

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        client = self._require_client()
        try:
            response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise PlankaClientError("Planka API timed out") from exc
        except httpx.HTTPError as exc:
            raise PlankaClientError("Planka API request failed") from exc
        return self._handle_response(response)

    async def _post_multipart(
        self,
        path: str,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
    ) -> Any:
        client = self._require_client()
        try:
            response = await client.post(path, data=data, files=files)
        except httpx.TimeoutException as exc:
            raise PlankaClientError("Planka API timed out") from exc
        except httpx.HTTPError as exc:
            raise PlankaClientError("Planka API request failed") from exc
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: httpx.Response) -> Any:
        if response.status_code == 401:
            raise PlankaAuthError("Planka authentication failed")
        if response.status_code == 403:
            raise PlankaClientError(f"Planka API forbidden: {response.text[:200]}")
        if response.is_error:
            raise PlankaClientError(
                f"Planka API returned {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            url = getattr(response.request, "url", None) or "?"
            logger.warning(
                "Planka API returned invalid JSON: url=%s status=%s content_type=%s",
                url,
                response.status_code,
                response.headers.get("content-type", "unknown"),
            )
            raise PlankaClientError("Planka API returned invalid JSON") from exc

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PlankaClient was used before start() was called")
        return self._client
