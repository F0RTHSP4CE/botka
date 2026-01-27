"""Configuration management for the bot - Extended version with all modules."""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class ThreadIdPair(BaseModel):
    """A chat/thread ID pair."""

    chat: int
    thread: int


class ResidentOwned(BaseModel):
    """Resident-owned chat configuration."""

    id: int
    internal: bool = False


class ForwardPins(BaseModel):
    """Forward pins configuration."""

    from_chat: int = Field(alias="from")
    to: int
    ignore_threads: list[int] = Field(default_factory=list)


class VortexConfig(BaseModel):
    """Vortex of doom (inactive topic archiving) configuration."""

    enabled: bool = False
    archive_topic_id: Optional[int] = None


class VortexOfDoom(BaseModel):
    """Vortex of doom configuration."""

    schedule: str = "0 7 * * 2"  # Every Tuesday at 07:00 (cron format)
    chat: ThreadIdPair
    additional_text: Optional[str] = None


class TelegramChats(BaseModel):
    """Telegram chat configuration."""

    residential: list[int] = Field(default_factory=list)
    borrowed_items: list[ThreadIdPair] = Field(default_factory=list)
    dashboard: Optional[ThreadIdPair] = None
    forward_channel: Optional[int] = None
    forward_pins: list[ForwardPins] = Field(default_factory=list)
    needs: Optional[ThreadIdPair] = None
    mac_monitoring: Optional[ThreadIdPair] = None
    ask_to_visit: Optional[ThreadIdPair] = None
    resident_owned: list[ResidentOwned] = Field(default_factory=list)
    wikijs_updates: Optional[ThreadIdPair] = None
    vortex_of_doom: Optional[VortexOfDoom] = None
    vortex: Optional[VortexConfig] = None


class TelegramConfig(BaseModel):
    """Telegram configuration."""

    token: str
    admins: list[int] = Field(default_factory=list)
    passive_mode: bool = False
    chats: TelegramChats = Field(default_factory=TelegramChats)


class MikrotikConfig(BaseModel):
    """Mikrotik router configuration."""

    host: str
    username: str
    password: str
    scheme: str = "http"


class EspCamConfig(BaseModel):
    """ESP camera configuration."""

    url: str


class ButlerConfig(BaseModel):
    """Butler (door opener) configuration."""

    url: str
    token: str


class WikiJsConfig(BaseModel):
    """Wiki.js configuration."""

    url: str
    token: str
    welcome_message_page: str = "/en/residents/welcome-message"
    dashboard_page: str = "/en/residents/topic-index"


class OpenAIConfig(BaseModel):
    """OpenAI/LLM configuration."""

    api_key: str
    api_base: Optional[str] = "https://openrouter.ai/api/v1"
    model: str = "google/gemini-2.5-flash-preview"
    disable: bool = False


class LdapAttributes(BaseModel):
    """LDAP attribute configuration."""

    user_class: str = "forthspacePerson"
    telegram_id: str = "telegramId"
    group_class: str = "groupOfUniqueNames"
    group_member: str = "uniqueMember"
    resident_group: str = "residents"


class LdapConfig(BaseModel):
    """LDAP configuration."""

    domain: str
    port: Optional[int] = 389
    tls: bool = False
    verify_cert: bool = False
    user: str
    password: str
    base_dn: str
    groups_dn: str = "ou=groups"
    users_dn: str = "ou=users"
    attributes: LdapAttributes = Field(default_factory=LdapAttributes)


class BorrowedItemsReminders(BaseModel):
    """Borrowed items reminder configuration."""

    check_interval_hours: int = 6
    overdue_after_hours: int = 48
    max_reminders: int = 3
    reminder_interval_hours: int = 48


class BorrowedItemsConfig(BaseModel):
    """Borrowed items configuration."""

    reminders: BorrowedItemsReminders = Field(default_factory=BorrowedItemsReminders)


class NlpConfig(BaseModel):
    """NLP configuration."""

    enabled: bool = True
    trigger_words: list[str] = Field(
        default_factory=lambda: ["bot", "бот", "botka", "ботка"]
    )
    models: list[str] = Field(
        default_factory=lambda: [
            "openai/gpt-4.1-nano",
            "openai/gpt-4.1-mini",
            "openai/gpt-4.1",
        ]
    )
    search_model: str = "openai/gpt-4o-mini-search-preview"
    classification_model: str = "google/gemini-2.0-flash-lite-001"
    max_history: int = 30
    memory_limit: int = 168  # hours (7 days)
    random_answer_probability: float = 3.33


class ServicesConfig(BaseModel):
    """External services configuration."""

    mikrotik: Optional[MikrotikConfig] = None
    racovina_cam: Optional[EspCamConfig] = None
    hlam_cam: Optional[EspCamConfig] = None
    vortex_of_doom_cam: Optional[EspCamConfig] = None
    butler: Optional[ButlerConfig] = None
    wikijs: Optional[WikiJsConfig] = None
    openai: Optional[OpenAIConfig] = None
    ldap: Optional[LdapConfig] = None


class Config(BaseModel):
    """Root configuration structure."""

    telegram: TelegramConfig
    server_addr: str = "0.0.0.0:8080"
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    nlp: NlpConfig = Field(default_factory=NlpConfig)
    borrowed_items: BorrowedItemsConfig = Field(default_factory=BorrowedItemsConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    return Config(**data)
