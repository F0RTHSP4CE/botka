from __future__ import annotations

from botka.config import Settings


def test_allowed_anon_group_ids_parse_from_environment_format() -> None:
    settings = Settings(
        _env_file=None,
        bot_token="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allowed_anon_group_ids="-100123, -100456",
    )

    assert settings.allowed_anon_group_ids == [-100123, -100456]
