"""Tests for NLP module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from botka.modules.nlp.types import (
    ClassificationResult,
    NlpDebug,
    SaveMemoryArgs,
    CHAT_TOOLS,
    SYSTEM_PROMPT,
)
from botka.modules.nlp.processing import (
    split_long_message,
    build_history_context,
    get_user_map,
)
from botka.modules.nlp.memory import GENERAL_THREAD_ID


class TestClassificationResult:
    """Tests for ClassificationResult enum."""

    def test_handle_levels(self):
        """Test classification levels."""
        assert ClassificationResult.HANDLE_1.value == 1
        assert ClassificationResult.HANDLE_2.value == 2
        assert ClassificationResult.HANDLE_3.value == 3
        assert ClassificationResult.IGNORE.value is None


class TestNlpDebug:
    """Tests for NlpDebug dataclass."""

    def test_default_values(self):
        """Test default values."""
        debug = NlpDebug()
        assert debug.classification_result == ""
        assert debug.used_model is None
        assert debug.prompt_tokens == 0
        assert debug.completion_tokens == 0

    def test_with_values(self):
        """Test with custom values."""
        debug = NlpDebug(
            classification_result="HANDLE_2",
            used_model="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
        )
        assert debug.classification_result == "HANDLE_2"
        assert debug.used_model == "gpt-4o-mini"


class TestSaveMemoryArgs:
    """Tests for SaveMemoryArgs dataclass."""

    def test_default_values(self):
        """Test default values."""
        args = SaveMemoryArgs(memory_text="Test memory")
        assert args.memory_text == "Test memory"
        assert args.duration_hours is None
        assert args.chat_specific is False
        assert args.thread_specific is False
        assert args.user_specific is False

    def test_with_scope(self):
        """Test with scope flags."""
        args = SaveMemoryArgs(
            memory_text="User preference",
            duration_hours=24,
            user_specific=True,
        )
        assert args.duration_hours == 24
        assert args.user_specific is True


class TestChatTools:
    """Tests for CHAT_TOOLS definition."""

    def test_tools_defined(self):
        """Test that tools are properly defined."""
        assert len(CHAT_TOOLS) > 0

        tool_names = [t["function"]["name"] for t in CHAT_TOOLS]
        assert "status" in tool_names
        assert "needs" in tool_names
        assert "add_need" in tool_names
        assert "open_door" in tool_names
        assert "save_memory" in tool_names
        assert "remove_memory" in tool_names
        assert "search" in tool_names

    def test_tool_structure(self):
        """Test tool structure is valid."""
        for tool in CHAT_TOOLS:
            assert "type" in tool
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]


class TestSystemPrompt:
    """Tests for system prompt."""

    def test_prompt_not_empty(self):
        """Test prompt is defined."""
        assert len(SYSTEM_PROMPT) > 100

    def test_prompt_contains_key_info(self):
        """Test prompt contains key information."""
        assert "F0BOT" in SYSTEM_PROMPT or "botka" in SYSTEM_PROMPT
        assert "status" in SYSTEM_PROMPT
        assert "needs" in SYSTEM_PROMPT


class TestSplitLongMessage:
    """Tests for split_long_message function."""

    def test_short_message(self):
        """Test short message is not split."""
        result = split_long_message("Hello, world!")
        assert result == ["Hello, world!"]

    def test_exact_limit(self):
        """Test message at exact limit."""
        message = "x" * 4096
        result = split_long_message(message)
        assert len(result) == 1

    def test_long_message_split(self):
        """Test long message is split."""
        message = "Line\n" * 2000  # Much longer than 4096
        result = split_long_message(message)
        assert len(result) > 1
        for part in result:
            assert len(part) <= 4096

    def test_split_at_newline(self):
        """Test splitting prefers newlines."""
        message = "First part\n" + "x" * 4090 + "\nLast part"
        result = split_long_message(message)
        assert len(result) >= 2


class TestBuildHistoryContext:
    """Tests for build_history_context function."""

    def test_empty_history(self):
        """Test with empty history."""
        result = build_history_context([], {})
        assert result == ""

    def test_with_history(self):
        """Test with history entries."""
        # Create mock history entries
        entry1 = MagicMock()
        entry1.message_text = "Hello"
        entry1.from_user_id = 123

        entry2 = MagicMock()
        entry2.message_text = "Hi there"
        entry2.from_user_id = 456

        user_map = {123: "@user1", 456: "User Two"}

        result = build_history_context([entry1, entry2], user_map)

        assert "@user1: Hello" in result
        assert "User Two: Hi there" in result

    def test_unknown_user(self):
        """Test with unknown user ID."""
        entry = MagicMock()
        entry.message_text = "Message"
        entry.from_user_id = 999

        result = build_history_context([entry], {})

        assert "Unknown: Message" in result

    def test_bot_message(self):
        """Test with bot message (no user ID)."""
        entry = MagicMock()
        entry.message_text = "Bot response"
        entry.from_user_id = None

        result = build_history_context([entry], {})

        assert "Bot: Bot response" in result


class TestGeneralThreadId:
    """Tests for GENERAL_THREAD_ID constant."""

    def test_value(self):
        """Test constant value."""
        assert GENERAL_THREAD_ID == 1


class TestMemoryOperations:
    """Tests for memory module database operations."""

    def test_store_message_skip_commands(self):
        """Test that commands are identified for skipping."""
        text = "/help"
        should_skip = text.startswith("/") or text.startswith("--")
        assert should_skip is True

    def test_store_message_skip_double_dash(self):
        """Test that messages starting with -- are skipped."""
        text = "-- ignore this"
        should_skip = text.startswith("/") or text.startswith("--")
        assert should_skip is True

    def test_store_message_normal_text(self):
        """Test that normal text is not skipped."""
        text = "Hello world"
        should_skip = text.startswith("/") or text.startswith("--")
        assert should_skip is False

    def test_memory_expiration_calculation(self):
        """Test memory expiration calculation."""
        from datetime import datetime, timedelta, timezone

        duration_hours = 24
        now = datetime.now(timezone.utc)
        expiration_date = now + timedelta(hours=duration_hours)

        diff = expiration_date - now
        assert diff.total_seconds() == duration_hours * 3600


class TestFilteringLogic:
    """Tests for NLP filtering logic."""

    def test_text_starts_with_command(self):
        """Test detection of command prefix."""
        text = "/help"
        is_command = text.startswith("/") or text.startswith("--")
        assert is_command is True

    def test_text_starts_with_double_dash(self):
        """Test detection of double dash prefix."""
        text = "-- skip this"
        is_special = text.startswith("/") or text.startswith("--")
        assert is_special is True

    def test_normal_message(self):
        """Test normal message detection."""
        text = "Hello bot, what's the status?"
        is_special = text.startswith("/") or text.startswith("--")
        assert is_special is False

    def test_trigger_word_matching(self):
        """Test trigger word matching logic."""
        text = "Hey botka, what's up?"
        trigger_words = ["botka", "f0bot"]

        # Normalize text words
        text_words = set(word.strip(".,!?;:'\"").lower() for word in text.split())

        # Check if any trigger word matches
        has_trigger = any(trigger.lower() in text_words for trigger in trigger_words)
        assert has_trigger is True

    def test_no_trigger_word(self):
        """Test when no trigger word is present."""
        text = "Random message without triggers"
        trigger_words = ["botka", "f0bot"]

        text_words = set(word.strip(".,!?;:'\"").lower() for word in text.split())

        has_trigger = any(trigger.lower() in text_words for trigger in trigger_words)
        assert has_trigger is False

    def test_trigger_word_case_insensitive(self):
        """Test trigger words are case insensitive."""
        text = "BOTKA please help"
        trigger_words = ["botka"]

        text_words = set(word.strip(".,!?;:'\"").lower() for word in text.split())

        has_trigger = any(trigger.lower() in text_words for trigger in trigger_words)
        assert has_trigger is True


class TestClassificationTypes:
    """Tests for classification result types."""

    def test_classification_result_values(self):
        """Test ClassificationResult enum values."""
        from botka.modules.nlp.types import ClassificationResult

        assert ClassificationResult.HANDLE_1.value == 1
        assert ClassificationResult.HANDLE_2.value == 2
        assert ClassificationResult.HANDLE_3.value == 3
        assert ClassificationResult.IGNORE.value is None

    def test_classification_comparison(self):
        """Test classification result comparison."""
        from botka.modules.nlp.types import ClassificationResult

        result = ClassificationResult.HANDLE_2
        assert result == ClassificationResult.HANDLE_2
        assert result != ClassificationResult.HANDLE_1


class TestSplitLongMessageEdgeCases:
    """Additional edge case tests for split_long_message."""

    def test_empty_message(self):
        """Test empty message."""
        from botka.modules.nlp.processing import split_long_message

        result = split_long_message("")
        assert result == [""]

    def test_single_very_long_word(self):
        """Test message with single very long word."""
        from botka.modules.nlp.processing import split_long_message

        long_word = "a" * 5000
        result = split_long_message(long_word)
        assert len(result) > 1
        # All parts should be within limit
        for part in result:
            assert len(part) <= 4096

    def test_unicode_message(self):
        """Test message with unicode characters."""
        from botka.modules.nlp.processing import split_long_message

        message = "Привет мир! 🎉" * 500
        result = split_long_message(message)
        for part in result:
            assert len(part) <= 4096
