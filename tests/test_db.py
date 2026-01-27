"""Tests for database models."""

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from botka.db import (
    TgUser,
    Resident,
    UserMac,
    NeededItem,
    BorrowedItem,
    TrackedPoll,
    ChatHistory,
    Memory,
    TempOpenToken,
)


class TestTgUser:
    """Tests for TgUser model."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session):
        """Test creating a user."""
        user = TgUser(
            id=123456789,
            username="testuser",
            first_name="Test",
            last_name="User",
        )
        db_session.add(user)
        await db_session.commit()

        result = await db_session.execute(select(TgUser).where(TgUser.id == 123456789))
        fetched = result.scalar_one()

        assert fetched.username == "testuser"
        assert fetched.first_name == "Test"
        assert fetched.last_name == "User"

    @pytest.mark.asyncio
    async def test_user_without_username(self, db_session):
        """Test user can be created without username."""
        user = TgUser(
            id=987654321,
            first_name="NoUsername",
        )
        db_session.add(user)
        await db_session.commit()

        result = await db_session.execute(select(TgUser).where(TgUser.id == 987654321))
        fetched = result.scalar_one()

        assert fetched.username is None


class TestResident:
    """Tests for Resident model."""

    @pytest.mark.asyncio
    async def test_create_resident(self, db_session):
        """Test creating a resident."""
        resident = Resident(
            tg_id=123456789,
            begin_date=datetime.now(timezone.utc),
        )
        db_session.add(resident)
        await db_session.commit()

        result = await db_session.execute(
            select(Resident).where(Resident.tg_id == 123456789)
        )
        fetched = result.scalar_one()

        assert fetched.end_date is None

    @pytest.mark.asyncio
    async def test_end_residency(self, db_session):
        """Test ending residency."""
        resident = Resident(
            tg_id=111222333,
            begin_date=datetime.now(timezone.utc) - timedelta(days=30),
        )
        db_session.add(resident)
        await db_session.commit()

        # End residency
        resident.end_date = datetime.now(timezone.utc)
        await db_session.commit()

        result = await db_session.execute(
            select(Resident).where(Resident.tg_id == 111222333)
        )
        fetched = result.scalar_one()

        assert fetched.end_date is not None


class TestUserMac:
    """Tests for UserMac model."""

    @pytest.mark.asyncio
    async def test_add_mac(self, db_session):
        """Test adding MAC address."""
        mac = UserMac(tg_id=123456789, mac="AA:BB:CC:DD:EE:FF")
        db_session.add(mac)
        await db_session.commit()

        result = await db_session.execute(
            select(UserMac).where(UserMac.tg_id == 123456789)
        )
        fetched = result.scalar_one()

        assert fetched.mac == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_multiple_macs_per_user(self, db_session):
        """Test user can have multiple MACs."""
        mac1 = UserMac(tg_id=123456789, mac="AA:BB:CC:DD:EE:01")
        mac2 = UserMac(tg_id=123456789, mac="AA:BB:CC:DD:EE:02")
        db_session.add_all([mac1, mac2])
        await db_session.commit()

        result = await db_session.execute(
            select(UserMac).where(UserMac.tg_id == 123456789)
        )
        macs = result.scalars().all()

        assert len(macs) == 2


class TestNeededItem:
    """Tests for NeededItem model."""

    @pytest.mark.asyncio
    async def test_create_needed_item(self, db_session):
        """Test creating a needed item."""
        item = NeededItem(
            request_chat_id=-1001234567890,
            request_message_id=1,
            request_user_id=123456789,
            pinned_chat_id=-1001234567890,
            pinned_message_id=1,
            item="Milk",
        )
        db_session.add(item)
        await db_session.commit()

        result = await db_session.execute(
            select(NeededItem).where(NeededItem.item == "Milk")
        )
        fetched = result.scalar_one()

        assert fetched.buyer_user_id is None

    @pytest.mark.asyncio
    async def test_mark_item_bought(self, db_session):
        """Test marking item as bought."""
        item = NeededItem(
            request_chat_id=-1001234567890,
            request_message_id=2,
            request_user_id=123456789,
            pinned_chat_id=-1001234567890,
            pinned_message_id=2,
            item="Bread",
        )
        db_session.add(item)
        await db_session.commit()

        # Mark as bought
        item.buyer_user_id = 987654321
        await db_session.commit()

        result = await db_session.execute(
            select(NeededItem).where(NeededItem.item == "Bread")
        )
        fetched = result.scalar_one()

        assert fetched.buyer_user_id == 987654321


class TestMemory:
    """Tests for Memory model (NLP)."""

    @pytest.mark.asyncio
    async def test_create_persistent_memory(self, db_session):
        """Test creating persistent memory."""
        memory = Memory(
            content="User prefers dark mode",
            created_at=datetime.now(timezone.utc),
            user_id=123456789,
        )
        db_session.add(memory)
        await db_session.commit()

        result = await db_session.execute(
            select(Memory).where(Memory.user_id == 123456789)
        )
        fetched = result.scalar_one()

        assert fetched.content == "User prefers dark mode"
        assert fetched.expiration_date is None

    @pytest.mark.asyncio
    async def test_create_expiring_memory(self, db_session):
        """Test creating memory with expiration."""
        expiration = datetime.now(timezone.utc) + timedelta(hours=24)
        memory = Memory(
            content="Temporary note",
            created_at=datetime.now(timezone.utc),
            expiration_date=expiration,
            chat_id=-1001234567890,
        )
        db_session.add(memory)
        await db_session.commit()

        result = await db_session.execute(
            select(Memory).where(Memory.chat_id == -1001234567890)
        )
        fetched = result.scalar_one()

        assert fetched.expiration_date is not None


class TestTempOpenToken:
    """Tests for TempOpenToken model."""

    @pytest.mark.asyncio
    async def test_create_token(self, db_session):
        """Test creating temporary open token."""
        token = TempOpenToken(
            token="abc123xyz",
            resident_tg_id=123456789,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(token)
        await db_session.commit()

        result = await db_session.execute(
            select(TempOpenToken).where(TempOpenToken.token == "abc123xyz")
        )
        fetched = result.scalar_one()

        assert fetched.resident_tg_id == 123456789
        assert fetched.used_at is None
        assert fetched.guest_tg_id is None
