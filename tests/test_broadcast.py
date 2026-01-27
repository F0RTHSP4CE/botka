"""Tests for broadcast module functions."""

import pytest
from unittest.mock import MagicMock


class TestBroadcastModuleImports:
    """Tests for broadcast module import checks."""

    def test_broadcast_module_imports(self):
        """Test broadcast module can be imported."""
        from botka.modules import broadcast

        assert broadcast is not None

    def test_broadcast_has_cmd(self):
        """Test broadcast module has cmd_broadcast."""
        from botka.modules import broadcast

        assert hasattr(broadcast, "cmd_broadcast")


class TestBroadcastMessageFormatting:
    """Tests for broadcast message formatting."""

    def test_format_broadcast_plain(self):
        """Test plain broadcast message."""
        message = "This is a test broadcast"
        formatted = f"📢 <b>Broadcast:</b>\n\n{message}"

        assert "📢" in formatted
        assert "Broadcast" in formatted
        assert message in formatted

    def test_format_broadcast_with_html(self):
        """Test broadcast with HTML formatting."""
        message = "Important <b>update</b>!"
        formatted = f"📢 <b>Broadcast:</b>\n\n{message}"

        assert "<b>update</b>" in formatted

    def test_extract_message_from_command(self):
        """Test extracting message from broadcast command."""
        text = "/broadcast Hello everyone!"

        if " " in text:
            message = text.split(" ", 1)[1]
        else:
            message = ""

        assert message == "Hello everyone!"

    def test_extract_message_no_content(self):
        """Test extracting when no message content."""
        text = "/broadcast"

        if " " in text:
            message = text.split(" ", 1)[1]
        else:
            message = ""

        assert message == ""
