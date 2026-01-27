"""Tests for borrowed_items module."""

import pytest
from botka.modules.borrowed_items import parse_items_from_text, create_return_keyboard


class TestParseItemsFromText:
    """Tests for parse_items_from_text function."""

    def test_parse_dash_list(self):
        """Test parsing dash-prefixed list."""
        text = """- Screwdriver
- Hammer
- Pliers"""
        items = parse_items_from_text(text)
        assert items == ["Screwdriver", "Hammer", "Pliers"]

    def test_parse_bullet_list(self):
        """Test parsing bullet-prefixed list."""
        text = """• Item one
• Item two
• Item three"""
        items = parse_items_from_text(text)
        assert items == ["Item one", "Item two", "Item three"]

    def test_parse_asterisk_list(self):
        """Test parsing asterisk-prefixed list."""
        text = """* First item
* Second item"""
        items = parse_items_from_text(text)
        assert items == ["First item", "Second item"]

    def test_parse_numbered_list_dot(self):
        """Test parsing numbered list with dot."""
        text = """1. Laptop
2. Charger
3. Mouse"""
        items = parse_items_from_text(text)
        assert items == ["Laptop", "Charger", "Mouse"]

    def test_parse_numbered_list_paren(self):
        """Test parsing numbered list with parenthesis."""
        text = """1) Item A
2) Item B
3) Item C"""
        items = parse_items_from_text(text)
        assert items == ["Item A", "Item B", "Item C"]

    def test_parse_single_item(self):
        """Test parsing single item (no list format)."""
        text = "Soldering iron"
        items = parse_items_from_text(text)
        assert items == ["Soldering iron"]

    def test_parse_empty_text(self):
        """Test parsing empty text."""
        text = ""
        items = parse_items_from_text(text)
        assert items == []

    def test_parse_whitespace_only(self):
        """Test parsing whitespace-only text."""
        text = "   \n\n   "
        items = parse_items_from_text(text)
        assert items == []

    def test_parse_mixed_formats(self):
        """Test parsing mixed list formats."""
        text = """- Dash item
• Bullet item
* Asterisk item"""
        items = parse_items_from_text(text)
        assert len(items) == 3
        assert "Dash item" in items
        assert "Bullet item" in items
        assert "Asterisk item" in items

    def test_parse_with_empty_lines(self):
        """Test parsing list with empty lines."""
        text = """- Item 1

- Item 2

- Item 3"""
        items = parse_items_from_text(text)
        assert items == ["Item 1", "Item 2", "Item 3"]

    def test_parse_short_line_skipped(self):
        """Test that very short non-list lines don't become items."""
        text = "ab"  # Less than 3 chars
        items = parse_items_from_text(text)
        assert items == []

    def test_parse_multiline_single_item(self):
        """Test that non-list multiline text only captures first line."""
        text = """Power drill
Some description here
More text"""
        items = parse_items_from_text(text)
        # First line becomes item, subsequent non-list lines ignored
        assert items == ["Power drill"]

    def test_parse_strips_whitespace(self):
        """Test that items are stripped of whitespace."""
        text = """  - Padded item  
-   Another padded   """
        items = parse_items_from_text(text)
        assert items == ["Padded item", "Another padded"]


class TestCreateReturnKeyboard:
    """Tests for create_return_keyboard function."""

    def test_create_keyboard_all_not_returned(self):
        """Test keyboard creation with no returned items."""
        items = [
            {"name": "Item 1", "returned": None},
            {"name": "Item 2", "returned": None},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        assert len(keyboard.inline_keyboard) == 2
        # Should have return buttons
        assert "↩️" in keyboard.inline_keyboard[0][0].text
        assert "↩️" in keyboard.inline_keyboard[1][0].text

    def test_create_keyboard_some_returned(self):
        """Test keyboard creation with some returned items."""
        items = [
            {"name": "Item 1", "returned": "2024-01-01T10:00:00"},
            {"name": "Item 2", "returned": None},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        assert len(keyboard.inline_keyboard) == 2
        # First item returned
        assert "✅" in keyboard.inline_keyboard[0][0].text
        # Second item not returned
        assert "↩️" in keyboard.inline_keyboard[1][0].text

    def test_create_keyboard_all_returned(self):
        """Test keyboard creation with all returned items."""
        items = [
            {"name": "Item 1", "returned": "2024-01-01T10:00:00"},
            {"name": "Item 2", "returned": "2024-01-02T11:00:00"},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        assert len(keyboard.inline_keyboard) == 2
        assert "✅" in keyboard.inline_keyboard[0][0].text
        assert "✅" in keyboard.inline_keyboard[1][0].text

    def test_create_keyboard_truncates_long_names(self):
        """Test that long item names are truncated."""
        items = [
            {"name": "A" * 50, "returned": None},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        # Name should be truncated to 30 chars
        button_text = keyboard.inline_keyboard[0][0].text
        assert len(button_text) <= 33  # "↩️ " (3) + 30 chars

    def test_create_keyboard_callback_data(self):
        """Test that callback data is correctly formatted."""
        items = [
            {"name": "Test Item", "returned": None},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        callback_data = keyboard.inline_keyboard[0][0].callback_data
        assert callback_data == "borrowed:return:123:456:0"

    def test_create_keyboard_info_callback_for_returned(self):
        """Test that returned items have info callback."""
        items = [
            {"name": "Test Item", "returned": "2024-01-01"},
        ]
        keyboard = create_return_keyboard(items, 123, 456)

        callback_data = keyboard.inline_keyboard[0][0].callback_data
        assert callback_data == "borrowed:info:123:456:0"


class TestCallbackDataParsing:
    """Tests for borrowed items callback data parsing."""

    def test_parse_return_callback(self):
        """Test parsing return callback data."""
        data = "borrowed:return:123:456:0"
        parts = data.split(":")

        assert len(parts) == 5
        assert parts[0] == "borrowed"
        assert parts[1] == "return"
        assert int(parts[2]) == 123  # chat_id
        assert int(parts[3]) == 456  # user_msg_id
        assert int(parts[4]) == 0  # item_index

    def test_parse_info_callback(self):
        """Test parsing info callback data."""
        data = "borrowed:info:123:456:2"
        parts = data.split(":")

        assert len(parts) == 5
        assert parts[1] == "info"
        assert int(parts[4]) == 2

    def test_callback_prefix_check(self):
        """Test callback prefix checking."""
        assert "borrowed:return:123:456:0".startswith("borrowed:")
        assert not "poll:refresh:abc".startswith("borrowed:")

    def test_invalid_callback_length(self):
        """Test invalid callback data detection."""
        data = "borrowed:return:123"  # Missing parts
        parts = data.split(":")
        assert len(parts) != 5
