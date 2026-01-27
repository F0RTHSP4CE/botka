"""Tests for database models."""

import pytest
from datetime import datetime, timedelta

from botka.db import (
    TgUser,
    TgChat,
    TgUserInChat,
    TgChatTopic,
    Resident,
    UserMac,
    UserSshKey,
    NeededItem,
    BorrowedItem,
    TrackedPoll,
    ChatHistory,
    Memory,
)


class TestTgUser:
    """Tests for TgUser model."""

    def test_create_user_with_all_fields(self):
        """Test creating user with all fields."""
        user = TgUser(
            id=123456789, username="testuser", first_name="Test", last_name="User"
        )

        assert user.id == 123456789
        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.last_name == "User"

    def test_create_user_minimal(self):
        """Test creating user with minimal fields."""
        user = TgUser(id=123, first_name="Test")

        assert user.id == 123
        assert user.first_name == "Test"
        assert user.username is None
        assert user.last_name is None


class TestTgChat:
    """Tests for TgChat model."""

    def test_create_chat(self):
        """Test creating chat."""
        chat = TgChat(id=-100123456789, kind="supergroup", title="Test Group")

        assert chat.id == -100123456789
        assert chat.kind == "supergroup"
        assert chat.title == "Test Group"

    def test_private_chat(self):
        """Test private chat type."""
        chat = TgChat(id=123, kind="private", username="testuser")

        assert chat.kind == "private"
        assert chat.username == "testuser"


class TestTgChatTopic:
    """Tests for TgChatTopic model."""

    def test_create_topic(self):
        """Test creating chat topic."""
        topic = TgChatTopic(
            chat_id=-100123, topic_id=42, name="General Discussion", closed=False
        )

        assert topic.chat_id == -100123
        assert topic.topic_id == 42
        assert topic.name == "General Discussion"
        assert topic.closed is False

    def test_closed_topic(self):
        """Test closed topic."""
        topic = TgChatTopic(chat_id=-100123, topic_id=42, closed=True)

        assert topic.closed is True


class TestResident:
    """Tests for Resident model."""

    def test_create_resident(self):
        """Test creating resident."""
        now = datetime.utcnow()
        resident = Resident(tg_id=123456789, begin_date=now)

        assert resident.tg_id == 123456789
        assert resident.begin_date == now
        assert resident.end_date is None

    def test_ended_residency(self):
        """Test ended residency."""
        now = datetime.utcnow()
        end = now + timedelta(days=30)

        resident = Resident(tg_id=123, begin_date=now, end_date=end)

        assert resident.end_date == end


class TestUserMac:
    """Tests for UserMac model."""

    def test_create_user_mac(self):
        """Test creating user MAC."""
        user_mac = UserMac(tg_id=123, mac="AA:BB:CC:DD:EE:FF")

        assert user_mac.tg_id == 123
        assert user_mac.mac == "AA:BB:CC:DD:EE:FF"


class TestUserSshKey:
    """Tests for UserSshKey model."""

    def test_create_ssh_key(self):
        """Test creating user SSH key."""
        key = UserSshKey(tg_id=123, key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...")

        assert key.tg_id == 123
        assert key.key.startswith("ssh-rsa")


class TestNeededItem:
    """Tests for NeededItem model."""

    def test_create_needed_item(self):
        """Test creating needed item."""
        item = NeededItem(
            request_chat_id=-100123,
            request_message_id=456,
            request_user_id=789,
            pinned_chat_id=-100123,
            pinned_message_id=456,
            item="Milk",
        )

        assert item.item == "Milk"
        assert item.buyer_user_id is None

    def test_bought_item(self):
        """Test marking item as bought."""
        item = NeededItem(
            request_chat_id=-100123,
            request_message_id=456,
            request_user_id=789,
            pinned_chat_id=-100123,
            pinned_message_id=456,
            item="Bread",
            buyer_user_id=999,
        )

        assert item.buyer_user_id == 999


class TestBorrowedItem:
    """Tests for BorrowedItem model."""

    def test_create_borrowed_item(self):
        """Test creating borrowed item."""
        import json

        items_data = [
            {"name": "Screwdriver", "returned": None},
            {"name": "Hammer", "returned": None},
        ]

        borrowed = BorrowedItem(
            chat_id=-100123,
            user_message_id=456,
            thread_id=0,
            bot_message_id=457,
            user_id=789,
            items=json.dumps(items_data),
        )

        assert borrowed.chat_id == -100123
        assert borrowed.user_id == 789

        parsed_items = json.loads(borrowed.items)
        assert len(parsed_items) == 2
        assert parsed_items[0]["name"] == "Screwdriver"


class TestTrackedPoll:
    """Tests for TrackedPoll model."""

    def test_create_tracked_poll(self):
        """Test creating tracked poll."""
        import json

        poll = TrackedPoll(
            tg_poll_id="abc123",
            creator_id=456,
            info_chat_id=-100123,
            info_message_id=789,
            voted_users=json.dumps([]),
        )

        assert poll.tg_poll_id == "abc123"
        assert poll.creator_id == 456

        voted = json.loads(poll.voted_users)
        assert voted == []

    def test_poll_with_votes(self):
        """Test poll with voted users."""
        import json

        voted_ids = [111, 222, 333]
        poll = TrackedPoll(
            tg_poll_id="xyz789",
            creator_id=456,
            info_chat_id=-100123,
            info_message_id=789,
            voted_users=json.dumps(voted_ids),
        )

        voted = json.loads(poll.voted_users)
        assert len(voted) == 3


class TestChatHistory:
    """Tests for ChatHistory model."""

    def test_create_chat_history(self):
        """Test creating chat history entry."""
        now = datetime.utcnow()

        entry = ChatHistory(
            chat_id=-100123,
            topic_id=42,
            message_id=789,
            from_user_id=456,
            timestamp=now,
            message_text="Hello, world!",
        )

        assert entry.chat_id == -100123
        assert entry.topic_id == 42
        assert entry.message_text == "Hello, world!"

    def test_bot_message_no_user(self):
        """Test bot message without user ID."""
        entry = ChatHistory(
            chat_id=-100123,
            topic_id=1,
            message_id=789,
            from_user_id=None,
            timestamp=datetime.utcnow(),
            message_text="Bot response",
        )

        assert entry.from_user_id is None


class TestMemory:
    """Tests for Memory model."""

    def test_create_persistent_memory(self):
        """Test creating persistent memory."""
        memory = Memory(content="Important fact to remember")

        assert memory.content == "Important fact to remember"
        assert memory.expiration_date is None

    def test_create_expiring_memory(self):
        """Test creating expiring memory."""
        expires = datetime.utcnow() + timedelta(hours=24)

        memory = Memory(content="Temporary note", expiration_date=expires)

        assert memory.content == "Temporary note"
        assert memory.expiration_date == expires
