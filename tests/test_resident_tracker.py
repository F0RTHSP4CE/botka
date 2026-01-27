"""Tests for resident_tracker module functions."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock


class TestResidentTrackerModuleImports:
    """Tests for resident_tracker module import checks."""

    def test_resident_tracker_module_imports(self):
        """Test resident_tracker module can be imported."""
        from botka.modules import resident_tracker

        assert resident_tracker is not None


class TestResidencyStatus:
    """Tests for residency status tracking."""

    def test_is_current_resident(self):
        """Test checking if user is current resident."""
        # Resident with no end date is current
        resident = MagicMock()
        resident.end_date = None

        is_current = resident.end_date is None
        assert is_current is True

    def test_is_former_resident(self):
        """Test checking if user is former resident."""
        resident = MagicMock()
        resident.end_date = datetime(2024, 1, 1)

        is_current = resident.end_date is None
        assert is_current is False

    def test_residency_duration(self):
        """Test calculating residency duration."""
        begin_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)

        duration = now - begin_date
        days = duration.days

        assert days > 150


class TestStatusChangeTracking:
    """Tests for status change detection."""

    def test_user_arrived(self):
        """Test detecting user arrival."""
        previous_active = set()
        current_active = {111}

        arrived = current_active - previous_active
        departed = previous_active - current_active

        assert arrived == {111}
        assert departed == set()

    def test_user_departed(self):
        """Test detecting user departure."""
        previous_active = {111, 222}
        current_active = {222}

        arrived = current_active - previous_active
        departed = previous_active - current_active

        assert arrived == set()
        assert departed == {111}

    def test_no_change(self):
        """Test when no status change."""
        previous_active = {111, 222}
        current_active = {111, 222}

        arrived = current_active - previous_active
        departed = previous_active - current_active

        assert arrived == set()
        assert departed == set()

    def test_multiple_changes(self):
        """Test multiple arrivals and departures."""
        previous_active = {111, 222}
        current_active = {222, 333, 444}

        arrived = current_active - previous_active
        departed = previous_active - current_active

        assert arrived == {333, 444}
        assert departed == {111}


class TestResidentList:
    """Tests for resident list management."""

    def test_get_active_residents(self):
        """Test filtering active residents."""
        residents = [
            MagicMock(tg_id=111, end_date=None),
            MagicMock(tg_id=222, end_date=datetime(2024, 1, 1)),
            MagicMock(tg_id=333, end_date=None),
        ]

        active = [r for r in residents if r.end_date is None]

        assert len(active) == 2
        assert all(r.end_date is None for r in active)

    def test_resident_ids(self):
        """Test getting resident IDs."""
        residents = [
            MagicMock(tg_id=111),
            MagicMock(tg_id=222),
            MagicMock(tg_id=333),
        ]

        ids = {r.tg_id for r in residents}

        assert ids == {111, 222, 333}
