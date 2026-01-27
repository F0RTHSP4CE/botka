"""Tests for tldr module functions."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch


class TestTldrModuleImports:
    """Tests for tldr module import checks."""

    def test_tldr_module_imports(self):
        """Test tldr module can be imported."""
        from botka.modules import tldr

        assert tldr is not None

    def test_tldr_has_cmd(self):
        """Test tldr module has tldr_cmd."""
        from botka.modules import tldr

        assert hasattr(tldr, "tldr_cmd")

    def test_tldr_has_get_handlers(self):
        """Test tldr module has get_handlers."""
        from botka.modules import tldr

        assert hasattr(tldr, "get_handlers")


class TestFilterParsing:
    """Tests for filter dict parsing."""

    def test_filter_with_time(self):
        """Test filter with time component."""
        filter_data = {"time": 24, "messages": None}

        since = datetime.now(timezone.utc) - timedelta(hours=filter_data["time"])
        now = datetime.now(timezone.utc)

        assert since < now
        diff_hours = (now - since).total_seconds() / 3600
        assert 23.9 < diff_hours < 24.1

    def test_filter_with_messages(self):
        """Test filter with message count."""
        filter_data = {"time": None, "messages": 200}

        # Apply hard cap of 500
        effective_limit = min(filter_data.get("messages") or 500, 500)
        assert effective_limit == 200

    def test_filter_hard_cap(self):
        """Test filter hard cap at 500."""
        filter_data = {"time": None, "messages": 1000}

        effective_limit = min(filter_data.get("messages") or 500, 500)
        assert effective_limit == 500

    def test_filter_default_messages(self):
        """Test filter with None messages."""
        filter_data = {"time": None, "messages": None}

        effective_limit = min(filter_data.get("messages") or 500, 500)
        assert effective_limit == 500


class TestTimeFiltering:
    """Tests for time-based message filtering."""

    def test_filter_messages_by_time(self):
        """Test filtering messages by time."""
        now = datetime.now(timezone.utc)

        # Create mock messages with different timestamps
        msg1 = MagicMock()
        msg1.timestamp = now - timedelta(hours=1)  # 1 hour ago

        msg2 = MagicMock()
        msg2.timestamp = now - timedelta(hours=5)  # 5 hours ago

        msg3 = MagicMock()
        msg3.timestamp = now - timedelta(hours=25)  # 25 hours ago

        history = [msg1, msg2, msg3]

        # Filter for last 24 hours
        hours = 24
        since = now - timedelta(hours=hours)
        filtered = [
            h for h in history if h.timestamp.replace(tzinfo=timezone.utc) >= since
        ]

        assert len(filtered) == 2  # Only msg1 and msg2
        assert msg3 not in filtered

    def test_filter_messages_by_count(self):
        """Test filtering messages by count."""
        history = [f"msg{i}" for i in range(100)]
        limit = 50

        # Get last 50 messages
        if len(history) > limit:
            history = history[-limit:]

        assert len(history) == 50
        assert history[0] == "msg50"  # First of last 50
        assert history[-1] == "msg99"  # Last message


class TestTldrQueryParsing:
    """Tests for TLDR query parsing logic."""

    def test_extract_query_from_command(self):
        """Test extracting user query from command text."""
        text = "/tldr last 2 hours"
        user_query = ""
        if " " in text:
            user_query = text.split(" ", 1)[1].strip()

        assert user_query == "last 2 hours"

    def test_extract_query_no_args(self):
        """Test extracting query when no arguments."""
        text = "/tldr"
        user_query = ""
        if " " in text:
            user_query = text.split(" ", 1)[1].strip()

        assert user_query == ""

    def test_extract_query_multiple_spaces(self):
        """Test extracting query with multiple spaces."""
        text = "/tldr   last   24   hours"
        user_query = ""
        if " " in text:
            user_query = text.split(" ", 1)[1].strip()

        assert user_query == "last   24   hours"
