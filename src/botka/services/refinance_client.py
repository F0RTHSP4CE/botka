"""HTTP client for the Refinance API.

Botka uses the shared REFINANCE_SECRET_KEY to mint short-lived JWTs on
behalf of any entity, so no user-facing auth step is required.  When a
Telegram user first calls a /refinance command, botka looks for an entity
with the matching telegram_id.  If none is found but an entity with the
same username exists, the telegram_id is linked automatically
(zero-friction onboarding).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import httpx
import jwt

from botka.config import Settings

logger = logging.getLogger(__name__)


class RefinanceClient:
    """Long-lived HTTP client for the Refinance API (APP-scoped singleton)."""

    _ALGORITHM = "HS256"
    _TOKEN_TTL = int(timedelta(hours=1).total_seconds())

    def __init__(self, settings: Settings) -> None:
        self._api_url = (settings.refinance_api_url or "").rstrip("/")
        self._secret_key = settings.refinance_secret_key or ""
        self._bot_entity_id = settings.refinance_bot_entity_id

    @property
    def is_configured(self) -> bool:
        return bool(self._api_url and self._secret_key and self._bot_entity_id)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _make_token(self, entity_id: int) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": str(entity_id),
            "iat": now,
            "exp": now + self._TOKEN_TTL,
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._ALGORITHM)

    def _entity_headers(self, entity_id: int) -> dict[str, str]:
        return {"X-Token": self._make_token(entity_id)}

    def _bot_headers(self) -> dict[str, str]:
        if not self._bot_entity_id:
            raise RuntimeError("refinance_bot_entity_id is not configured")
        return self._entity_headers(self._bot_entity_id)

    def _raise_for_status(self, r: httpx.Response) -> None:
        """Like raise_for_status but logs the API error body first."""
        if r.is_error:
            try:
                body = r.json()
                logger.error(
                    "Refinance API error %s %s: %s",
                    r.status_code,
                    r.request.url,
                    body.get("error") or body,
                )
            except Exception:
                pass
            r.raise_for_status()

    async def _get(
        self,
        path: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> Any:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self._api_url}{path}",
                headers=headers,
                params=params,
            )
            self._raise_for_status(r)
            return r.json()

    async def _post(
        self,
        path: str,
        headers: dict[str, str],
        json: Any,
    ) -> Any:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api_url}{path}",
                headers=headers,
                json=json,
            )
            self._raise_for_status(r)
            return r.json()

    async def _patch(
        self,
        path: str,
        headers: dict[str, str],
        json: Any,
    ) -> Any:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{self._api_url}{path}",
                headers=headers,
                json=json,
            )
            self._raise_for_status(r)
            return r.json()

    # ------------------------------------------------------------------ #
    # Entity / auth                                                         #
    # ------------------------------------------------------------------ #

    async def verify_bot_entity(self) -> bool:
        """Check that the bot entity exists in the refinance DB.

        Called at startup so misconfig is caught early with a clear log.
        """
        if not self.is_configured:
            return False
        try:
            await self._get("/entities/me", self._bot_headers())
            return True
        except Exception as exc:
            logger.error(
                "Refinance bot entity %s is invalid — check BOTKA_REFINANCE_BOT_ENTITY_ID: %s",
                self._bot_entity_id,
                exc,
            )
            return False

    async def find_entity_by_telegram_id(self, telegram_id: int) -> dict | None:
        data = await self._get(
            "/entities",
            self._bot_headers(),
            {"auth_telegram_id": telegram_id, "limit": 1},
        )
        items = (data or {}).get("items", [])
        return items[0] if items else None

    async def find_entity_by_name(self, name: str) -> dict | None:
        data = await self._get(
            "/entities",
            self._bot_headers(),
            {"name": name, "limit": 1},
        )
        items = (data or {}).get("items", [])
        return items[0] if items else None

    async def link_telegram_id(self, entity_id: int, telegram_id: int) -> dict:
        """Attach telegram_id to an existing entity (auto-onboarding)."""
        return await self._patch(
            f"/entities/{entity_id}",
            self._entity_headers(entity_id),
            {"auth": {"telegram_id": telegram_id}},
        )

    async def get_or_link_entity(
        self, telegram_id: int, username: str | None = None
    ) -> dict | None:
        """Return the refinance entity for this Telegram user.

        On first call: if no entity is linked yet but one shares the same
        name as the Telegram username, it is linked automatically.
        """
        if not self.is_configured:
            return None
        entity = await self.find_entity_by_telegram_id(telegram_id)
        if entity:
            return entity
        if username:
            entity = await self.find_entity_by_name(username)
            if entity:
                await self.link_telegram_id(entity["id"], telegram_id)
                return entity
        return None

    # ------------------------------------------------------------------ #
    # Balance                                                               #
    # ------------------------------------------------------------------ #

    async def get_balance(self, entity_id: int) -> dict:
        return await self._get(
            f"/balances/{entity_id}",
            self._entity_headers(entity_id),
        )

    # ------------------------------------------------------------------ #
    # Transactions                                                          #
    # ------------------------------------------------------------------ #

    async def create_transaction(
        self,
        actor_entity_id: int,
        from_entity_id: int,
        to_entity_id: int,
        amount: str,
        currency: str,
        status: str = "completed",
        comment: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "from_entity_id": from_entity_id,
            "to_entity_id": to_entity_id,
            "amount": amount,
            "currency": currency.lower(),
            "status": status,
        }
        if comment:
            body["comment"] = comment
        return await self._post(
            "/transactions",
            self._entity_headers(actor_entity_id),
            body,
        )

    async def get_transactions(self, entity_id: int, limit: int = 10) -> list[dict]:
        data = await self._get(
            "/transactions",
            self._entity_headers(entity_id),
            {"entity_id": entity_id, "limit": limit, "skip": 0},
        )
        return (data or {}).get("items", [])

    async def update_transaction_status(
        self, actor_entity_id: int, transaction_id: int, status: str
    ) -> dict:
        return await self._patch(
            f"/transactions/{transaction_id}",
            self._entity_headers(actor_entity_id),
            {"status": status},
        )

    async def delete_transaction(self, actor_entity_id: int, transaction_id: int) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{self._api_url}/transactions/{transaction_id}",
                headers=self._entity_headers(actor_entity_id),
            )
            self._raise_for_status(r)

    # ------------------------------------------------------------------ #
    # Invoices                                                              #
    # ------------------------------------------------------------------ #

    async def get_invoices(
        self,
        entity_id: int,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "entity_id": entity_id,
            "limit": limit,
            "skip": 0,
        }
        if status:
            params["status"] = status
        data = await self._get(
            "/invoices",
            self._entity_headers(entity_id),
            params,
        )
        return (data or {}).get("items", [])

    # ------------------------------------------------------------------ #
    # Deposits                                                              #
    # ------------------------------------------------------------------ #

    async def create_keepz_deposit(
        self,
        entity_id: int,
        amount: str,
        currency: str,
    ) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._api_url}/deposits/providers/keepz",
                headers=self._entity_headers(entity_id),
                params={
                    "to_entity_id": entity_id,
                    "amount": amount,
                    "currency": currency.upper(),
                },
            )
            self._raise_for_status(r)
            return r.json()

    async def get_deposit(self, entity_id: int, deposit_id: int) -> dict:
        return await self._get(
            f"/deposits/{deposit_id}",
            self._entity_headers(entity_id),
        )
