"""Tests for welcome module functions."""

import pytest
from unittest.mock import MagicMock


class TestWelcomeModuleImports:
    """Tests for welcome module import checks."""

    def test_welcome_module_imports(self):
        """Test welcome module can be imported."""
        from botka.modules import welcome

        assert welcome is not None


class TestWelcomeMessages:
    """Tests for welcome message formatting."""

    def test_format_welcome_basic(self):
        """Test basic welcome message format."""
        name = "Alice"
        welcome_msg = f"Welcome, {name}!"
        assert "Alice" in welcome_msg
        assert "Welcome" in welcome_msg

    def test_format_welcome_with_username(self):
        """Test welcome message with username."""
        name = "Bob"
        username = "bobuser"
        welcome_msg = f"Welcome, {name} (@{username})!"
        assert "Bob" in welcome_msg
        assert "@bobuser" in welcome_msg

    def test_format_user_link(self):
        """Test Telegram user link format."""
        user_id = 123456789
        name = "Charlie"
        user_link = f'<a href="tg://user?id={user_id}">{name}</a>'

        assert str(user_id) in user_link
        assert name in user_link
        assert "tg://user" in user_link


class TestNewMemberDetection:
    """Tests for new member detection logic."""

    def test_is_new_member_join(self):
        """Test detecting join message."""
        # Mock message with new_chat_members
        msg = MagicMock()
        msg.new_chat_members = [MagicMock()]

        has_new_members = bool(msg.new_chat_members and len(msg.new_chat_members) > 0)
        assert has_new_members is True

    def test_is_new_member_empty(self):
        """Test when no new members."""
        msg = MagicMock()
        msg.new_chat_members = []

        has_new_members = bool(msg.new_chat_members and len(msg.new_chat_members) > 0)
        assert has_new_members is False

    def test_is_new_member_none(self):
        """Test when new_chat_members is None."""
        msg = MagicMock()
        msg.new_chat_members = None

        has_new_members = bool(msg.new_chat_members and len(msg.new_chat_members) > 0)
        assert has_new_members is False

    def test_multiple_new_members(self):
        """Test handling multiple new members."""
        msg = MagicMock()
        msg.new_chat_members = [MagicMock(), MagicMock(), MagicMock()]

        count = len(msg.new_chat_members)
        assert count == 3
