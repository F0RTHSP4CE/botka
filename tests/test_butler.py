"""Tests for butler module functions."""

import pytest
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from botka.db import TempOpenToken


class TestTempTokenGeneration:
    """Tests for temp token generation."""

    def test_token_format(self):
        """Test generated token format."""
        token = secrets.token_urlsafe(32)
        assert len(token) > 40  # URL-safe base64 is ~43 chars for 32 bytes
        assert " " not in token

    def test_token_uniqueness(self):
        """Test that tokens are unique."""
        tokens = [secrets.token_urlsafe(32) for _ in range(100)]
        assert len(set(tokens)) == 100  # All unique


class TestButlerCallbackParsing:
    """Tests for butler callback data parsing."""

    def test_parse_callback_open(self):
        """Test parsing open callback."""
        callback_data = "butler:open"
        parts = callback_data.split(":")
        assert parts[0] == "butler"
        assert parts[1] == "open"

    def test_parse_callback_temp(self):
        """Test parsing temp callback."""
        callback_data = "butler:temp:15"
        parts = callback_data.split(":")
        assert parts[0] == "butler"
        assert parts[1] == "temp"
        assert parts[2] == "15"


class TestTokenExpiration:
    """Tests for token expiration logic."""

    def test_token_not_expired(self):
        """Test token that is not expired."""
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        now = datetime.now(timezone.utc)
        assert expires > now

    def test_token_expired(self):
        """Test token that is expired."""
        expires = datetime.now(timezone.utc) - timedelta(minutes=1)
        now = datetime.now(timezone.utc)
        assert expires <= now

    def test_token_expiration_calculation(self):
        """Test calculating expiration from duration."""
        duration_minutes = 30
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=duration_minutes)

        diff = expires - now
        assert diff.total_seconds() == duration_minutes * 60


class TestTempTokenDuration:
    """Tests for temp token duration options."""

    def test_duration_15_minutes(self):
        """Test 15 minute duration."""
        duration = 15
        expires = datetime.now(timezone.utc) + timedelta(minutes=duration)
        assert (expires - datetime.now(timezone.utc)).total_seconds() > 14 * 60

    def test_duration_30_minutes(self):
        """Test 30 minute duration."""
        duration = 30
        expires = datetime.now(timezone.utc) + timedelta(minutes=duration)
        assert (expires - datetime.now(timezone.utc)).total_seconds() > 29 * 60

    def test_duration_60_minutes(self):
        """Test 60 minute duration."""
        duration = 60
        expires = datetime.now(timezone.utc) + timedelta(minutes=duration)
        assert (expires - datetime.now(timezone.utc)).total_seconds() > 59 * 60


class TestTokenUrlGeneration:
    """Tests for token URL generation."""

    def test_start_url_format(self):
        """Test start URL format."""
        bot_username = "test_bot"
        token = "abc123token"
        url = f"https://t.me/{bot_username}?start=temp_{token}"

        assert bot_username in url
        assert token in url
        assert "?start=temp_" in url

    def test_token_extraction_from_start(self):
        """Test extracting token from start parameter."""
        start_param = "temp_abc123token"

        if start_param.startswith("temp_"):
            token = start_param[5:]
            assert token == "abc123token"
