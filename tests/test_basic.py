"""Tests for basic module functions."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestFormatUser:
    """Tests for format_user functions in basic module."""

    def test_format_user_with_username(self):
        """Test formatting user with username."""
        from botka.modules.basic import format_user

        user = MagicMock()
        user.first_name = "John"
        user.last_name = "Doe"
        user.username = "johndoe"

        result = format_user(user)
        assert result == "John Doe (@johndoe)"

    def test_format_user_without_username(self):
        """Test formatting user without username."""
        from botka.modules.basic import format_user

        user = MagicMock()
        user.first_name = "Jane"
        user.last_name = "Smith"
        user.username = None

        result = format_user(user)
        assert result == "Jane Smith"

    def test_format_user_first_name_only(self):
        """Test formatting user with first name only."""
        from botka.modules.basic import format_user

        user = MagicMock()
        user.first_name = "Alice"
        user.last_name = None
        user.username = None

        result = format_user(user)
        assert result == "Alice"

    def test_format_user_html_basic(self):
        """Test HTML formatting of user."""
        from botka.modules.basic import format_user_html

        user = MagicMock()
        user.first_name = "Bob"
        user.last_name = "Builder"
        user.id = 12345

        result = format_user_html(user)
        assert '<a href="tg://user?id=12345">' in result
        assert "Bob Builder" in result
        assert "</a>" in result

    def test_format_user_html_first_name_only(self):
        """Test HTML formatting with first name only."""
        from botka.modules.basic import format_user_html

        user = MagicMock()
        user.first_name = "Charlie"
        user.last_name = None
        user.id = 99999

        result = format_user_html(user)
        assert "tg://user?id=99999" in result
        assert "Charlie" in result
        assert "None" not in result


class TestUserFormatting:
    """Additional user formatting tests."""

    def test_format_user_special_chars(self):
        """Test formatting with special characters."""
        from botka.modules.basic import format_user

        user = MagicMock()
        user.first_name = "José"
        user.last_name = "García"
        user.username = "jose_garcia"

        result = format_user(user)
        assert "José García" in result
        assert "@jose_garcia" in result

    def test_format_user_html_escaping(self):
        """Test that HTML formatting handles various names."""
        from botka.modules.basic import format_user_html

        user = MagicMock()
        user.first_name = "Test"
        user.last_name = "User"
        user.id = 1

        result = format_user_html(user)
        assert "Test User" in result
        assert "tg://user?id=1" in result
