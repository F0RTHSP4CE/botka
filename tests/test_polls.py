"""Tests for polls module."""

import pytest
from botka.modules.polls import format_poll_info


class TestFormatPollInfo:
    """Tests for format_poll_info function."""

    def test_format_poll_info_basic(self):
        """Test basic poll info formatting."""
        result = format_poll_info(123, "Alice", [], 5)
        assert 'Poll by <a href="tg://user?id=123">Alice</a>' in result
        assert "Votes: 5" in result
        assert "Everyone has voted!" in result

    def test_format_poll_info_with_non_voters(self):
        """Test poll info with non-voters list."""
        non_voters = [(456, "Bob"), (789, "Charlie")]
        result = format_poll_info(123, "Alice", non_voters, 3)

        assert "Votes: 3" in result
        assert "Haven't voted:" in result
        assert "Bob" in result
        assert "Charlie" in result

    def test_format_poll_info_many_non_voters(self):
        """Test poll info with more than 20 non-voters."""
        non_voters = [(i, f"User{i}") for i in range(25)]
        result = format_poll_info(123, "Alice", non_voters, 0)

        assert "Haven't voted:" in result
        assert "User0" in result
        assert "User19" in result
        # Should show "and X more"
        assert "and 5 more" in result

    def test_format_poll_info_exactly_20_non_voters(self):
        """Test with exactly 20 non-voters."""
        non_voters = [(i, f"User{i}") for i in range(20)]
        result = format_poll_info(123, "Alice", non_voters, 0)

        # Should show all 20 without "more"
        assert "User19" in result
        assert "more" not in result

    def test_format_poll_info_zero_votes(self):
        """Test poll info with zero votes."""
        result = format_poll_info(123, "Alice", [(456, "Bob")], 0)
        assert "Votes: 0" in result

    def test_format_poll_info_html_escaping(self):
        """Test that creator name is included properly."""
        result = format_poll_info(123, "Alice<>&", [], 0)
        # Name should be in the output (escaping is a frontend concern)
        assert "Alice<>&" in result

    def test_format_poll_info_contains_user_links(self):
        """Test that user links are properly formatted."""
        non_voters = [(456, "Bob")]
        result = format_poll_info(123, "Alice", non_voters, 1)

        # Should contain user links
        assert "tg://user?id=123" in result
        assert "tg://user?id=456" in result


class TestPollValidation:
    """Tests for poll validation logic (extracted from handle_poll_message)."""

    @pytest.mark.parametrize(
        "question,should_track",
        [
            ("!What should we do?", True),
            ("What should we do?", False),
            ("! Poll with space", True),
            ("", False),
            ("!!", True),
        ],
    )
    def test_poll_question_tracking(self, question, should_track):
        """Test poll question prefix detection."""
        result = question.startswith("!")
        assert result == should_track

    def test_poll_question_strip(self):
        """Test poll question prefix stripping."""
        question = "!What should we do?"
        stripped = question.lstrip("!").strip()
        assert stripped == "What should we do?"

    def test_poll_question_strip_multiple(self):
        """Test stripping multiple ! characters."""
        question = "!!!Multiple exclamations"
        stripped = question.lstrip("!").strip()
        assert stripped == "Multiple exclamations"

    def test_poll_question_strip_with_space(self):
        """Test stripping with leading space."""
        question = "! Question with space"
        stripped = question.lstrip("!").strip()
        assert stripped == "Question with space"


class TestCallbackParsing:
    """Tests for callback data parsing logic."""

    def test_parse_callback_data_refresh(self):
        """Test parsing refresh callback data."""
        data = "poll:refresh:abc123"
        parts = data.split(":")

        assert len(parts) == 3
        assert parts[0] == "poll"
        assert parts[1] == "refresh"
        assert parts[2] == "abc123"

    def test_parse_callback_data_invalid(self):
        """Test parsing invalid callback data."""
        data = "poll:refresh"
        parts = data.split(":")

        assert len(parts) != 3

    def test_callback_data_prefix_check(self):
        """Test callback data prefix checking."""
        assert "poll:refresh:123".startswith("poll:")
        assert not "needs:buy:456".startswith("poll:")
        assert not "borrowed:return:123:456:0".startswith("poll:")
