"""Tests for configuration module."""

import pytest
import tempfile
import os
from pathlib import Path

from botka.config import load_config, Config, TelegramConfig


def test_config_model_creation():
    """Test that Config model can be created."""
    config = Config(
        telegram=TelegramConfig(
            token="test_token",
            admins=[123],
        ),
    )
    assert config.telegram.token == "test_token"
    assert config.telegram.admins == [123]
    assert config.telegram.passive_mode is False


def test_config_defaults():
    """Test configuration default values."""
    config = Config(
        telegram=TelegramConfig(token="test"),
    )
    assert config.server_addr == "0.0.0.0:8080"
    assert config.telegram.passive_mode is False
    assert config.nlp.enabled is True
    assert config.nlp.max_history == 30


def test_load_config_from_file():
    """Test loading configuration from YAML file."""
    yaml_content = """
telegram:
  token: "test_token_from_file"
  admins: [111, 222]
  passive_mode: true
  chats:
    residential: [-100111, -100222]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = load_config(f.name)
            assert config.telegram.token == "test_token_from_file"
            assert config.telegram.admins == [111, 222]
            assert config.telegram.passive_mode is True
            assert config.telegram.chats.residential == [-100111, -100222]
        finally:
            os.unlink(f.name)


def test_load_config_file_not_found():
    """Test that loading non-existent config raises error."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_config_with_services():
    """Test configuration with services."""
    yaml_content = """
telegram:
  token: "test"
services:
  mikrotik:
    host: "192.168.1.1"
    username: "admin"
    password: "secret"
    scheme: "https"
  butler:
    url: "http://butler.local/control"
    token: "butler_token"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = load_config(f.name)
            assert config.services.mikrotik is not None
            assert config.services.mikrotik.host == "192.168.1.1"
            assert config.services.mikrotik.scheme == "https"
            assert config.services.butler.token == "butler_token"
        finally:
            os.unlink(f.name)


def test_config_with_nlp():
    """Test configuration with NLP settings."""
    yaml_content = """
telegram:
  token: "test"
nlp:
  enabled: true
  trigger_words: ["bot", "помощь"]
  models: ["gpt-4o-mini", "gpt-4o"]
  max_history: 50
  random_answer_probability: 5.0
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = load_config(f.name)
            assert config.nlp.enabled is True
            assert "bot" in config.nlp.trigger_words
            assert "помощь" in config.nlp.trigger_words
            assert len(config.nlp.models) == 2
            assert config.nlp.max_history == 50
            assert config.nlp.random_answer_probability == 5.0
        finally:
            os.unlink(f.name)
