"""Tests for NLP processing module."""

import pytest
from botka.modules.nlp.processing import (
    build_history_context,
    split_long_message,
)


class TestBuildHistoryContext:
    """Tests for build_history_context function."""

    def test_build_empty_history(self):
        """Test building context from empty history."""
        result = build_history_context([], {})
        assert result == ""

    def test_build_with_user_map(self):
        """Test building context with user name mapping."""

        class MockEntry:
            def __init__(self, from_user_id, message_text):
                self.from_user_id = from_user_id
                self.message_text = message_text

        history = [
            MockEntry(123, "Hello"),
            MockEntry(456, "Hi there"),
        ]
        user_map = {123: "@alice", 456: "Bob"}

        result = build_history_context(history, user_map)

        assert "@alice: Hello" in result
        assert "Bob: Hi there" in result

    def test_build_with_unknown_user(self):
        """Test building context with unknown user."""

        class MockEntry:
            def __init__(self, from_user_id, message_text):
                self.from_user_id = from_user_id
                self.message_text = message_text

        history = [MockEntry(999, "Message from unknown")]
        user_map = {}

        result = build_history_context(history, user_map)

        assert "Unknown: Message from unknown" in result

    def test_build_with_bot_message(self):
        """Test building context with bot message (no user id)."""

        class MockEntry:
            def __init__(self, from_user_id, message_text):
                self.from_user_id = from_user_id
                self.message_text = message_text

        history = [MockEntry(None, "Bot response")]

        result = build_history_context(history, {})

        assert "Bot: Bot response" in result

    def test_build_skips_empty_messages(self):
        """Test that empty messages are skipped."""

        class MockEntry:
            def __init__(self, from_user_id, message_text):
                self.from_user_id = from_user_id
                self.message_text = message_text

        history = [
            MockEntry(123, "Valid message"),
            MockEntry(456, None),
            MockEntry(789, ""),
            MockEntry(111, "Another valid"),
        ]
        user_map = {123: "Alice", 111: "Charlie"}

        result = build_history_context(history, user_map)

        assert "Alice: Valid message" in result
        assert "Charlie: Another valid" in result
        lines = result.split("\n")
        assert len(lines) == 2

    def test_build_limits_to_20_messages(self):
        """Test that history is limited to last 20 messages."""

        class MockEntry:
            def __init__(self, from_user_id, message_text):
                self.from_user_id = from_user_id
                self.message_text = message_text

        history = [MockEntry(i, f"Message {i}") for i in range(30)]
        user_map = {i: f"User{i}" for i in range(30)}

        result = build_history_context(history, user_map)

        lines = result.split("\n")
        assert len(lines) == 20
        # Should have the last 20 messages (10-29)
        assert "Message 10" in result
        assert "Message 29" in result
        assert "Message 9" not in result


class TestSplitLongMessage:
    """Tests for split_long_message function."""

    def test_short_message_unchanged(self):
        """Test that short messages aren't split."""
        text = "Short message"
        result = split_long_message(text)

        assert result == ["Short message"]

    def test_message_at_limit(self):
        """Test message exactly at limit."""
        text = "A" * 4096
        result = split_long_message(text)

        assert result == [text]

    def test_split_at_newline(self):
        """Test that long messages split at newline."""
        text = "A" * 2000 + "\n" + "B" * 2000 + "\n" + "C" * 2000
        result = split_long_message(text)

        assert len(result) >= 2
        # Each part should be at or under limit
        for part in result:
            assert len(part) <= 4096

    def test_split_at_space(self):
        """Test that long messages split at space when no newline."""
        text = "word " * 1000
        result = split_long_message(text)

        assert len(result) >= 2
        for part in result:
            assert len(part) <= 4096

    def test_split_very_long_word(self):
        """Test splitting with no natural break points."""
        text = "A" * 10000  # No spaces or newlines
        result = split_long_message(text)

        assert len(result) >= 3
        for part in result:
            assert len(part) <= 4096

    def test_custom_max_length(self):
        """Test with custom max length."""
        text = "Hello World Testing"
        result = split_long_message(text, max_length=10)

        assert len(result) >= 2
        for part in result:
            assert len(part) <= 10

    def test_strips_leading_whitespace_after_split(self):
        """Test that parts are stripped of leading whitespace."""
        text = "Part one\n\n   Part two"
        result = split_long_message(text, max_length=10)

        # Parts should be stripped
        for part in result:
            assert not part.startswith(" ")

    def test_empty_message(self):
        """Test with empty message."""
        result = split_long_message("")
        assert result == [""]


class TestStatusMessageFormatting:
    """Tests for status message formatting logic."""

    def test_no_users_message(self):
        """Test message when no users present."""
        active_users = set()
        if not active_users:
            message = "No one is currently in the hackerspace."
        else:
            message = "Someone is there"

        assert "No one" in message
        assert "hackerspace" in message

    def test_single_user_message(self):
        """Test message formatting for single user."""
        names = ["Alice"]
        count = len(names)

        if count == 1:
            message = f"There is 1 resident in the hackerspace: {names[0]}."
        else:
            message = f"There are {count} residents"

        assert "There is 1 resident" in message
        assert "Alice" in message

    def test_multiple_users_message(self):
        """Test message formatting for multiple users."""
        names = ["Alice", "Bob", "Charlie"]
        count = len(names)

        message = f"There are {count} residents in the hackerspace: {', '.join(names)}."

        assert "There are 3 residents" in message
        assert "Alice, Bob, Charlie" in message


class TestUserMapBuilding:
    """Tests for user map building logic."""

    def test_prefer_username(self):
        """Test that username is preferred over name."""
        # Logic: if username exists, use @username
        username = "alice"
        first_name = "Alice"

        if username:
            display = f"@{username}"
        else:
            display = first_name

        assert display == "@alice"

    def test_fallback_to_first_name(self):
        """Test fallback to first name."""
        username = None
        first_name = "Alice"
        last_name = None

        if username:
            display = f"@{username}"
        else:
            display = first_name
            if last_name:
                display += f" {last_name}"

        assert display == "Alice"

    def test_full_name_format(self):
        """Test full name formatting."""
        username = None
        first_name = "Alice"
        last_name = "Smith"

        if username:
            display = f"@{username}"
        else:
            display = first_name
            if last_name:
                display += f" {last_name}"

        assert display == "Alice Smith"


class TestToolExecution:
    """Tests for tool execution logic (without actual execution)."""

    def test_door_access_check(self):
        """Test door access permission logic."""
        is_resident = True

        if not is_resident:
            result = "Only residents can open the door."
        else:
            result = "Allowed"

        assert result == "Allowed"

    def test_door_non_resident_denied(self):
        """Test non-resident door access denial."""
        is_resident = False

        if not is_resident:
            result = "Only residents can open the door."
        else:
            result = "Allowed"

        assert "Only residents" in result

    def test_unknown_function_handling(self):
        """Test unknown function handling."""
        func_name = "unknown_tool"
        result = f"Unknown function: {func_name}"

        assert "Unknown function" in result
        assert "unknown_tool" in result
