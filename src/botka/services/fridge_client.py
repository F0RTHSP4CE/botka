"""HTTP client for the Fridge POS remote-charge API."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from botka.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChargeResult:
    ok: bool
    unlocked: bool
    charged: bool
    amount: float | None = None
    currency: str | None = None
    balance_completed: float | None = None
    balance_draft: float | None = None
    error: str | None = None


class FridgeClient:
    """Thin wrapper around the Fridge POS remote-charge endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = (settings.fridge_pos_url or "").rstrip("/")
        self._secret = settings.fridge_pos_secret or ""

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._secret)

    async def remote_charge(self, entity_name: str) -> ChargeResult:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._base_url}/remote-charge",
                headers={"X-POS-Secret": self._secret},
                data={"entity_name": entity_name},
            )
            if r.status_code == 403:
                return ChargeResult(
                    ok=False,
                    unlocked=False,
                    charged=False,
                    error="Authentication failed",
                )
            if r.status_code == 400:
                return ChargeResult(
                    ok=False, unlocked=False, charged=False, error="Bad request"
                )
            r.raise_for_status()
            body = r.json()
            return ChargeResult(
                ok=body.get("ok", False),
                unlocked=body.get("unlocked", False),
                charged=body.get("charged", False),
                amount=body.get("amount"),
                currency=body.get("currency"),
                balance_completed=body.get("balance_completed"),
                balance_draft=body.get("balance_draft"),
                error=body.get("error"),
            )
