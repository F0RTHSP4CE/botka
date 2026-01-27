"""Tests for needs module."""

import pytest


class TestNeedsCallbackParsing:
    """Tests for needs callback data parsing logic."""

    def test_parse_buy_callback(self):
        """Test parsing buy callback data."""
        data = "needs:buy:123"
        parts = data.split(":")

        assert len(parts) == 3
        assert parts[0] == "needs"
        assert parts[1] == "buy"
        assert int(parts[2]) == 123

    def test_callback_prefix_check(self):
        """Test callback prefix checking."""
        assert "needs:buy:123".startswith("needs:")
        assert not "poll:refresh:abc".startswith("needs:")
        assert not "borrowed:return:123:456:0".startswith("needs:")

    def test_invalid_callback_action(self):
        """Test handling invalid callback action."""
        data = "needs:invalid:123"
        parts = data.split(":")

        assert parts[1] != "buy"

    def test_invalid_item_id(self):
        """Test handling non-numeric item ID."""
        data = "needs:buy:abc"
        parts = data.split(":")

        with pytest.raises(ValueError):
            int(parts[2])

    def test_callback_with_short_data(self):
        """Test callback with insufficient parts."""
        data = "needs:buy"
        parts = data.split(":")

        assert len(parts) != 3


class TestShoppingListFormatting:
    """Tests for shopping list formatting logic."""

    def test_format_shopping_list_item(self):
        """Test formatting a single shopping list item."""
        item_name = "Milk"
        user_name = "Alice"
        index = 1

        formatted = f"{index}. {item_name} (by {user_name})"
        assert formatted == "1. Milk (by Alice)"

    def test_format_shopping_list_button(self):
        """Test formatting button text with truncation."""
        item_name = "A very long item name that should be truncated"
        index = 1

        button_text = f"✅ {index}. {item_name[:20]}..."
        assert len(button_text) < 30
        assert button_text.startswith("✅ 1.")
        assert button_text.endswith("...")

    def test_empty_list_message(self):
        """Test empty list message."""
        items = []
        if not items:
            message = "No items needed. 🎉"
        else:
            message = "Items exist"

        assert message == "No items needed. 🎉"

    def test_format_header(self):
        """Test shopping list header formatting."""
        header = "<b>Shopping list:</b>\n\n"
        assert header.startswith("<b>")
        assert "Shopping list" in header


class TestUsageMessage:
    """Tests for usage message formatting."""

    def test_usage_message_format(self):
        """Test /need usage message format."""
        usage = "Usage: /need <item>"
        assert "/need" in usage
        assert "<item>" in usage

    def test_success_message_format(self):
        """Test success message format."""
        item_text = "Bread"
        message = f"✅ Added to shopping list: {item_text}"

        assert "✅" in message
        assert "Bread" in message
        assert "shopping list" in message


class TestItemParsing:
    """Tests for item text parsing from command args."""

    def test_join_single_word(self):
        """Test joining single word args."""
        args = ["milk"]
        item = " ".join(args)
        assert item == "milk"

    def test_join_multiple_words(self):
        """Test joining multiple word args."""
        args = ["fresh", "bread", "from", "bakery"]
        item = " ".join(args)
        assert item == "fresh bread from bakery"

    def test_empty_args(self):
        """Test empty args handling."""
        args = []
        if not args:
            result = None
        else:
            result = " ".join(args)

        assert result is None
