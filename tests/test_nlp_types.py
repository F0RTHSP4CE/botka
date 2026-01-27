"""Tests for NLP types module."""

import pytest
from botka.modules.nlp.types import (
    ClassificationResult,
    NlpDebug,
    SaveMemoryArgs,
    RemoveMemoryArgs,
    AddNeedArgs,
    SearchArgs,
    CHAT_TOOLS,
    SYSTEM_PROMPT,
)


class TestClassificationResult:
    """Tests for ClassificationResult enum."""

    def test_handle_1_value(self):
        """Test HANDLE_1 has correct value."""
        assert ClassificationResult.HANDLE_1.value == 1

    def test_handle_2_value(self):
        """Test HANDLE_2 has correct value."""
        assert ClassificationResult.HANDLE_2.value == 2

    def test_handle_3_value(self):
        """Test HANDLE_3 has correct value."""
        assert ClassificationResult.HANDLE_3.value == 3

    def test_ignore_value(self):
        """Test IGNORE has None value."""
        assert ClassificationResult.IGNORE.value is None

    def test_all_enum_members(self):
        """Test all enum members exist."""
        members = [m.name for m in ClassificationResult]
        assert "HANDLE_1" in members
        assert "HANDLE_2" in members
        assert "HANDLE_3" in members
        assert "IGNORE" in members
        assert len(members) == 4


class TestNlpDebug:
    """Tests for NlpDebug dataclass."""

    def test_create_default(self):
        """Test creating with defaults."""
        debug = NlpDebug()

        assert debug.classification_result == ""
        assert debug.used_model is None
        assert debug.prompt_tokens == 0
        assert debug.completion_tokens == 0

    def test_create_with_values(self):
        """Test creating with values."""
        debug = NlpDebug(
            classification_result="HANDLE_2",
            used_model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert debug.classification_result == "HANDLE_2"
        assert debug.used_model == "gpt-4"
        assert debug.prompt_tokens == 100
        assert debug.completion_tokens == 50


class TestSaveMemoryArgs:
    """Tests for SaveMemoryArgs dataclass."""

    def test_create_minimal(self):
        """Test creating with only required field."""
        args = SaveMemoryArgs(memory_text="Remember this")

        assert args.memory_text == "Remember this"
        assert args.duration_hours is None
        assert args.chat_specific is False
        assert args.thread_specific is False
        assert args.user_specific is False

    def test_create_with_duration(self):
        """Test creating with duration."""
        args = SaveMemoryArgs(memory_text="Temporary note", duration_hours=24)

        assert args.duration_hours == 24

    def test_create_chat_specific(self):
        """Test creating chat-specific memory."""
        args = SaveMemoryArgs(memory_text="Chat memory", chat_specific=True)

        assert args.chat_specific is True

    def test_create_thread_specific(self):
        """Test creating thread-specific memory."""
        args = SaveMemoryArgs(memory_text="Thread memory", thread_specific=True)

        assert args.thread_specific is True

    def test_create_user_specific(self):
        """Test creating user-specific memory."""
        args = SaveMemoryArgs(memory_text="User memory", user_specific=True)

        assert args.user_specific is True


class TestRemoveMemoryArgs:
    """Tests for RemoveMemoryArgs dataclass."""

    def test_create(self):
        """Test creating remove memory args."""
        args = RemoveMemoryArgs(memory_id=42)
        assert args.memory_id == 42

    def test_memory_id_types(self):
        """Test various memory ID values."""
        args1 = RemoveMemoryArgs(memory_id=0)
        args2 = RemoveMemoryArgs(memory_id=999999)

        assert args1.memory_id == 0
        assert args2.memory_id == 999999


class TestAddNeedArgs:
    """Tests for AddNeedArgs dataclass."""

    def test_create(self):
        """Test creating add need args."""
        args = AddNeedArgs(item="Milk")
        assert args.item == "Milk"

    def test_item_with_spaces(self):
        """Test item with spaces."""
        args = AddNeedArgs(item="Fresh bread from bakery")
        assert args.item == "Fresh bread from bakery"


class TestSearchArgs:
    """Tests for SearchArgs dataclass."""

    def test_create(self):
        """Test creating search args."""
        args = SearchArgs(query="how to use 3D printer")
        assert args.query == "how to use 3D printer"

    def test_empty_query(self):
        """Test empty query."""
        args = SearchArgs(query="")
        assert args.query == ""


class TestChatTools:
    """Tests for CHAT_TOOLS constant."""

    def test_tools_is_list(self):
        """Test that CHAT_TOOLS is a list."""
        assert isinstance(CHAT_TOOLS, list)

    def test_tools_not_empty(self):
        """Test that tools list is not empty."""
        assert len(CHAT_TOOLS) > 0

    def test_tool_structure(self):
        """Test that each tool has correct structure."""
        for tool in CHAT_TOOLS:
            assert "type" in tool
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]

    def test_save_memory_tool_exists(self):
        """Test that save_memory tool is defined."""
        tool_names = [t["function"]["name"] for t in CHAT_TOOLS]
        assert "save_memory" in tool_names

    def test_remove_memory_tool_exists(self):
        """Test that remove_memory tool is defined."""
        tool_names = [t["function"]["name"] for t in CHAT_TOOLS]
        assert "remove_memory" in tool_names

    def test_status_tool_exists(self):
        """Test that status tool is defined."""
        tool_names = [t["function"]["name"] for t in CHAT_TOOLS]
        assert "status" in tool_names

    def test_needs_tool_exists(self):
        """Test that needs tool is defined."""
        tool_names = [t["function"]["name"] for t in CHAT_TOOLS]
        assert "needs" in tool_names


class TestSystemPrompt:
    """Tests for SYSTEM_PROMPT constant."""

    def test_system_prompt_is_string(self):
        """Test that SYSTEM_PROMPT is a string."""
        assert isinstance(SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self):
        """Test that SYSTEM_PROMPT is not empty."""
        assert len(SYSTEM_PROMPT) > 0

    def test_system_prompt_contains_role(self):
        """Test that system prompt defines the assistant role."""
        # The prompt should define what the assistant is
        assert (
            "hackerspace" in SYSTEM_PROMPT.lower()
            or "assistant" in SYSTEM_PROMPT.lower()
        )
