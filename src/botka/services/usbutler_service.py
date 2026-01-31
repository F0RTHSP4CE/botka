from __future__ import annotations

import httpx

from botka.config import Settings


class UsbutlerService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = (
            settings.usbutler_base_url.rstrip("/")
            if settings.usbutler_base_url
            else None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._settings.usbutler_api_key)

    async def open_door(self, door_id: int, username: str) -> bool:
        if not self.is_configured:
            return False
        timeout = httpx.Timeout(self._settings.usbutler_timeout_seconds)
        headers = {"X-Api-Key": self._settings.usbutler_api_key}
        url = f"{self._base_url}/api/doors/{door_id}/open"
        payload = {"username": username}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
        return response.is_success
