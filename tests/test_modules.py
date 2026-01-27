"""Tests for bot modules."""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from botka.db import (
    NeededItem,
    BorrowedItem,
    TrackedPoll,
    TempOpenToken,
    TgUser,
    Resident,
    get_session,
)


class TestNeedsModule:
    """Tests for needs module."""

    def test_needs_module_imports(self):
        """Test needs module can be imported."""
        from botka.modules import needs

        assert needs is not None
        assert hasattr(needs, "cmd_needs")
        assert hasattr(needs, "cmd_need")
        assert hasattr(needs, "needs_callback")

    @pytest.mark.asyncio
    async def test_needs_callback_buy_item(self, db_session):
        """Test marking item as bought via callback."""
        # Create a needed item
        item = NeededItem(
            request_chat_id=-100123,
            request_message_id=1,
            request_user_id=111,
            pinned_chat_id=-100123,
            pinned_message_id=1,
            item="Test Item",
        )
        db_session.add(item)
        await db_session.commit()

        # Verify item exists and is not bought
        result = await db_session.execute(
            select(NeededItem).where(NeededItem.item == "Test Item")
        )
        fetched = result.scalar_one()
        assert fetched.buyer_user_id is None

        # Simulate buying
        fetched.buyer_user_id = 222
        await db_session.commit()

        # Verify bought
        result = await db_session.execute(
            select(NeededItem).where(NeededItem.item == "Test Item")
        )
        fetched = result.scalar_one()
        assert fetched.buyer_user_id == 222


class TestButlerModule:
    """Tests for butler module."""

    def test_butler_module_imports(self):
        """Test butler module can be imported."""
        from botka.modules import butler

        assert butler is not None
        assert hasattr(butler, "cmd_open")
        assert hasattr(butler, "cmd_temp_open")
        assert hasattr(butler, "handle_start_temp")

    @pytest.mark.asyncio
    async def test_temp_open_token_creation(self, db_session):
        """Test creating temporary open token."""
        import secrets

        token_str = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)

        token = TempOpenToken(
            token=token_str,
            resident_tg_id=123456789,
            expires_at=expires,
        )
        db_session.add(token)
        await db_session.commit()

        # Verify token exists
        result = await db_session.execute(
            select(TempOpenToken).where(TempOpenToken.token == token_str)
        )
        fetched = result.scalar_one()
        assert fetched.resident_tg_id == 123456789
        assert fetched.guest_tg_id is None
        assert fetched.used_at is None

    @pytest.mark.asyncio
    async def test_temp_open_token_activation(self, db_session):
        """Test activating token for guest."""
        import secrets

        token_str = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=15)

        token = TempOpenToken(
            token=token_str,
            resident_tg_id=123456789,
            expires_at=expires,
        )
        db_session.add(token)
        await db_session.commit()

        # Activate for guest
        result = await db_session.execute(
            select(TempOpenToken).where(TempOpenToken.token == token_str)
        )
        fetched = result.scalar_one()
        fetched.guest_tg_id = 987654321
        await db_session.commit()

        # Verify activation
        result = await db_session.execute(
            select(TempOpenToken).where(TempOpenToken.token == token_str)
        )
        fetched = result.scalar_one()
        assert fetched.guest_tg_id == 987654321

    @pytest.mark.asyncio
    async def test_expired_token_check(self, db_session):
        """Test that expired tokens can be detected."""
        import secrets

        token_str = secrets.token_urlsafe(32)
        # Token expired 1 hour ago
        expires = datetime.now(timezone.utc) - timedelta(hours=1)

        token = TempOpenToken(
            token=token_str,
            resident_tg_id=123456789,
            expires_at=expires,
        )
        db_session.add(token)
        await db_session.commit()

        # Query for valid tokens only
        now = datetime.now(timezone.utc)
        result = await db_session.execute(
            select(TempOpenToken).where(
                TempOpenToken.token == token_str,
                TempOpenToken.expires_at > now,
            )
        )
        fetched = result.scalar_one_or_none()
        assert fetched is None  # Token should not be found (expired)


class TestPollsModule:
    """Tests for polls module."""

    def test_polls_module_imports(self):
        """Test polls module can be imported."""
        from botka.modules import polls

        assert polls is not None
        assert hasattr(polls, "handle_poll_message")
        assert hasattr(polls, "handle_poll_answer")
        assert hasattr(polls, "format_poll_info")
        assert hasattr(polls, "get_non_voters")

    def test_format_poll_info(self):
        """Test poll info message formatting."""
        from botka.modules.polls import format_poll_info

        non_voters = [(123, "Alice"), (456, "Bob")]
        result = format_poll_info(111, "Creator", non_voters, 5)

        assert "Poll by" in result
        assert "Creator" in result
        assert "Votes: 5" in result
        assert "Haven't voted:" in result
        assert "Alice" in result
        assert "Bob" in result

    def test_format_poll_info_all_voted(self):
        """Test poll info when everyone voted."""
        from botka.modules.polls import format_poll_info

        result = format_poll_info(111, "Creator", [], 10)

        assert "Everyone has voted!" in result

    def test_parse_poll_question_with_exclamation(self):
        """Test parsing poll question starting with !."""
        # Poll questions starting with ! are tracked
        question = "! Should we buy pizza?"
        assert question.startswith("!")
        clean_question = question.lstrip("!").strip()
        assert clean_question == "Should we buy pizza?"

    @pytest.mark.asyncio
    async def test_tracked_poll_creation(self, db_session):
        """Test creating tracked poll record."""
        poll = TrackedPoll(
            tg_poll_id="poll_123abc",
            creator_id=123456789,
            info_chat_id=-100123,
            info_message_id=42,
            voted_users=json.dumps([]),
        )
        db_session.add(poll)
        await db_session.commit()

        result = await db_session.execute(
            select(TrackedPoll).where(TrackedPoll.tg_poll_id == "poll_123abc")
        )
        fetched = result.scalar_one()
        assert fetched.creator_id == 123456789
        assert json.loads(fetched.voted_users) == []

    @pytest.mark.asyncio
    async def test_tracked_poll_vote_tracking(self, db_session):
        """Test tracking votes on poll."""
        poll = TrackedPoll(
            tg_poll_id="poll_456def",
            creator_id=111,
            info_chat_id=-100123,
            info_message_id=43,
            voted_users=json.dumps([]),
        )
        db_session.add(poll)
        await db_session.commit()

        # Add votes
        result = await db_session.execute(
            select(TrackedPoll).where(TrackedPoll.tg_poll_id == "poll_456def")
        )
        fetched = result.scalar_one()

        voted = json.loads(fetched.voted_users)
        voted.append(222)
        voted.append(333)
        fetched.voted_users = json.dumps(voted)
        await db_session.commit()

        # Verify votes
        result = await db_session.execute(
            select(TrackedPoll).where(TrackedPoll.tg_poll_id == "poll_456def")
        )
        fetched = result.scalar_one()
        voted = json.loads(fetched.voted_users)
        assert 222 in voted
        assert 333 in voted
        assert len(voted) == 2


class TestBorrowedItemsModule:
    """Tests for borrowed items module."""

    def test_borrowed_items_module_imports(self):
        """Test borrowed_items module can be imported."""
        from botka.modules import borrowed_items

        assert borrowed_items is not None
        assert hasattr(borrowed_items, "parse_items_from_text")
        assert hasattr(borrowed_items, "create_return_keyboard")

    def test_parse_items_bullet_list(self):
        """Test parsing bullet point list."""
        from botka.modules.borrowed_items import parse_items_from_text

        text = """- Drill
- Hammer
- Screwdriver"""

        items = parse_items_from_text(text)
        assert len(items) == 3
        assert "Drill" in items
        assert "Hammer" in items
        assert "Screwdriver" in items

    def test_parse_items_numbered_list(self):
        """Test parsing numbered list."""
        from botka.modules.borrowed_items import parse_items_from_text

        text = """1. Soldering iron
2. Multimeter
3. Wire strippers"""

        items = parse_items_from_text(text)
        assert len(items) == 3
        assert "Soldering iron" in items
        assert "Multimeter" in items
        assert "Wire strippers" in items

    def test_parse_items_asterisk_list(self):
        """Test parsing asterisk list."""
        from botka.modules.borrowed_items import parse_items_from_text

        text = """* Item A
* Item B"""

        items = parse_items_from_text(text)
        assert len(items) == 2
        assert "Item A" in items
        assert "Item B" in items

    def test_parse_items_single_line(self):
        """Test parsing single line item."""
        from botka.modules.borrowed_items import parse_items_from_text

        text = "Oscilloscope"
        items = parse_items_from_text(text)
        assert len(items) == 1
        assert "Oscilloscope" in items

    def test_parse_items_empty(self):
        """Test parsing empty text."""
        from botka.modules.borrowed_items import parse_items_from_text

        items = parse_items_from_text("")
        assert items == []

    def test_create_return_keyboard(self):
        """Test creating return keyboard."""
        from botka.modules.borrowed_items import create_return_keyboard

        items = [
            {"name": "Drill", "returned": None},
            {"name": "Hammer", "returned": "2024-01-01"},
        ]

        keyboard = create_return_keyboard(items, -100123, 42)

        assert keyboard is not None
        assert len(keyboard.inline_keyboard) == 2
        # First item not returned - has return button
        assert "↩️" in keyboard.inline_keyboard[0][0].text
        # Second item returned - has check mark
        assert "✅" in keyboard.inline_keyboard[1][0].text

    @pytest.mark.asyncio
    async def test_borrowed_item_creation(self, db_session):
        """Test creating borrowed item record."""
        items_data = [
            {"name": "Drill", "returned": None},
            {"name": "Hammer", "returned": None},
        ]

        borrowed = BorrowedItem(
            chat_id=-100123,
            user_message_id=1,
            thread_id=0,
            bot_message_id=2,
            user_id=123456789,
            items=json.dumps(items_data),
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(borrowed)
        await db_session.commit()

        result = await db_session.execute(
            select(BorrowedItem).where(BorrowedItem.user_id == 123456789)
        )
        fetched = result.scalar_one()
        items = json.loads(fetched.items)
        assert len(items) == 2
        assert items[0]["name"] == "Drill"

    @pytest.mark.asyncio
    async def test_borrowed_item_return(self, db_session):
        """Test marking item as returned."""
        items_data = [{"name": "Drill", "returned": None}]

        borrowed = BorrowedItem(
            chat_id=-100123,
            user_message_id=10,
            thread_id=0,
            bot_message_id=11,
            user_id=111,
            items=json.dumps(items_data),
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(borrowed)
        await db_session.commit()

        # Mark as returned
        result = await db_session.execute(
            select(BorrowedItem).where(BorrowedItem.user_message_id == 10)
        )
        fetched = result.scalar_one()
        items = json.loads(fetched.items)
        items[0]["returned"] = datetime.now(timezone.utc).isoformat()
        fetched.items = json.dumps(items)
        await db_session.commit()

        # Verify
        result = await db_session.execute(
            select(BorrowedItem).where(BorrowedItem.user_message_id == 10)
        )
        fetched = result.scalar_one()
        items = json.loads(fetched.items)
        assert items[0]["returned"] is not None


class TestWelcomeModule:
    """Tests for welcome module."""

    def test_welcome_module_imports(self):
        """Test welcome module can be imported."""
        from botka.modules import welcome

        assert welcome is not None


class TestCameraModule:
    """Tests for camera module."""

    def test_camera_module_imports(self):
        """Test camera module can be imported."""
        from botka.modules import camera

        assert camera is not None
        assert hasattr(camera, "cmd_racovina")
        assert hasattr(camera, "cmd_hlam")


class TestTldrModule:
    """Tests for tldr module."""

    def test_tldr_module_imports(self):
        """Test tldr module can be imported."""
        from botka.modules import tldr

        assert hasattr(tldr, "get_handlers")
        assert hasattr(tldr, "tldr_cmd")


class TestAskToVisitModule:
    """Tests for ask_to_visit module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import ask_to_visit

        assert ask_to_visit is not None


class TestBroadcastModule:
    """Tests for broadcast module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import broadcast

        assert broadcast is not None


class TestUserctlModule:
    """Tests for userctl module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import userctl

        assert userctl is not None


class TestMacMonitoringModule:
    """Tests for mac_monitoring module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import mac_monitoring

        assert mac_monitoring is not None


class TestResidentTrackerModule:
    """Tests for resident_tracker module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import resident_tracker

        assert resident_tracker is not None


class TestVortexOfDoomModule:
    """Tests for vortex_of_doom module."""

    def test_module_imports(self):
        """Test module can be imported."""
        from botka.modules import vortex_of_doom

        assert vortex_of_doom is not None
        assert hasattr(vortex_of_doom, "get_handlers")
