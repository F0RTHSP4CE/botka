from __future__ import annotations

from typing import Iterable

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    shopping_chat_id: int | None = None
    shopping_topic_id: int | None = None
    borrowed_chat_id: int | None = None
    borrowed_topic_id: int | None = None
    pins_chat_id: int | None = None
    pins_tracked_chat_ids: list[int] | str = Field(default_factory=list)
    bootstrap_resident_ids: list[int] | str = Field(default_factory=list)
    usbutler_base_url: str | None = None
    usbutler_api_key: str | None = None
    usbutler_timeout_seconds: float = 5.0
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 10.0
    mac_tracker_base_url: str | None = None
    mac_tracker_bind_host: str = "0.0.0.0"
    mac_tracker_bind_port: int = 1818
    mac_tracker_poll_seconds: float = 30.0
    mac_tracker_jwt_secret: str | None = None
    mac_tracker_jwt_ttl_seconds: int = 900
    mac_tracker_notify_chat_id: int | None = None
    mac_tracker_notify_topic_id: int | None = None
    mikrotik_base_url: str | None = None
    mikrotik_username: str | None = None
    mikrotik_password: str | None = None
    mikrotik_timeout_seconds: float = 5.0
    mikrotik_verify_tls: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BOTKA_",
        case_sensitive=False,
    )

    @field_validator("bootstrap_resident_ids", mode="before")
    @classmethod
    def parse_bootstrap_ids(cls, value: str | int | Iterable[int] | None) -> list[int]:
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",") if part.strip()]
            return [int(part) for part in items]
        return list(value)

    @field_validator("pins_tracked_chat_ids", mode="before")
    @classmethod
    def parse_pins_tracked_ids(
        cls, value: str | int | Iterable[int] | None
    ) -> list[int]:
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",") if part.strip()]
            return [int(part) for part in items]
        return list(value)

    @field_validator(
        "shopping_topic_id",
        "shopping_chat_id",
        "borrowed_topic_id",
        "borrowed_chat_id",
        "pins_chat_id",
        "mac_tracker_notify_chat_id",
        "mac_tracker_notify_topic_id",
        mode="before",
    )
    @classmethod
    def parse_optional_int(cls, value: str | int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            return int(value)
        return value
