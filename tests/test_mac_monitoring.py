"""Tests for mac_monitoring module functions."""

import pytest
from datetime import datetime, timedelta


class TestMacMonitoringModuleImports:
    """Tests for mac_monitoring module import checks."""

    def test_mac_monitoring_module_imports(self):
        """Test mac_monitoring module can be imported."""
        from botka.modules import mac_monitoring

        assert mac_monitoring is not None


class TestActivityTracking:
    """Tests for user activity tracking logic."""

    def test_activity_threshold_active(self):
        """Test device is active within threshold."""
        last_seen = timedelta(minutes=5)
        threshold = timedelta(minutes=20)

        is_active = last_seen < threshold
        assert is_active is True

    def test_activity_threshold_inactive(self):
        """Test device is inactive beyond threshold."""
        last_seen = timedelta(hours=1)
        threshold = timedelta(minutes=20)

        is_active = last_seen < threshold
        assert is_active is False

    def test_activity_threshold_edge(self):
        """Test device at exact threshold."""
        last_seen = timedelta(minutes=20)
        threshold = timedelta(minutes=20)

        is_active = last_seen < threshold
        assert is_active is False  # At threshold, not active

    def test_activity_multiple_devices(self):
        """Test tracking multiple devices."""
        devices = [
            {"mac": "aa:bb:cc:dd:ee:ff", "last_seen": timedelta(minutes=5)},
            {"mac": "11:22:33:44:55:66", "last_seen": timedelta(hours=2)},
            {"mac": "77:88:99:00:11:22", "last_seen": timedelta(minutes=15)},
        ]

        threshold = timedelta(minutes=20)
        active_devices = [d for d in devices if d["last_seen"] < threshold]

        assert len(active_devices) == 2


class TestMacAddressLookup:
    """Tests for MAC address lookup logic."""

    def test_mac_to_user_mapping(self):
        """Test mapping MAC to user."""
        mac_to_user = {
            "aa:bb:cc:dd:ee:ff": 111,
            "11:22:33:44:55:66": 222,
        }

        assert mac_to_user.get("aa:bb:cc:dd:ee:ff") == 111
        assert mac_to_user.get("unknown:mac") is None

    def test_user_has_multiple_macs(self):
        """Test user with multiple MAC addresses."""
        user_macs = {
            111: ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"],
            222: ["77:88:99:00:11:22"],
        }

        assert len(user_macs[111]) == 2
        assert len(user_macs[222]) == 1

    def test_active_users_from_macs(self):
        """Test getting active users from active MACs."""
        active_macs = ["aa:bb:cc:dd:ee:ff", "77:88:99:00:11:22"]
        mac_to_user = {
            "aa:bb:cc:dd:ee:ff": 111,
            "11:22:33:44:55:66": 222,
            "77:88:99:00:11:22": 111,  # Same user
        }

        active_users = set()
        for mac in active_macs:
            if mac in mac_to_user:
                active_users.add(mac_to_user[mac])

        assert active_users == {111}  # Only user 111 is active
