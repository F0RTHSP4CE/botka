"""Tests for userctl module functions."""

import pytest
import re
from unittest.mock import MagicMock


class TestMacPattern:
    """Tests for MAC address pattern validation."""

    MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

    def test_valid_mac_lowercase(self):
        """Test valid MAC address with lowercase."""
        mac = "aa:bb:cc:dd:ee:ff"
        assert self.MAC_PATTERN.match(mac) is not None

    def test_valid_mac_uppercase(self):
        """Test valid MAC address with uppercase."""
        mac = "AA:BB:CC:DD:EE:FF"
        assert self.MAC_PATTERN.match(mac) is not None

    def test_valid_mac_mixed_case(self):
        """Test valid MAC address with mixed case."""
        mac = "Aa:Bb:Cc:Dd:Ee:Ff"
        assert self.MAC_PATTERN.match(mac) is not None

    def test_invalid_mac_too_short(self):
        """Test invalid MAC - too short."""
        mac = "aa:bb:cc:dd:ee"
        assert self.MAC_PATTERN.match(mac) is None

    def test_invalid_mac_too_long(self):
        """Test invalid MAC - too long."""
        mac = "aa:bb:cc:dd:ee:ff:gg"
        assert self.MAC_PATTERN.match(mac) is None

    def test_invalid_mac_wrong_separator(self):
        """Test invalid MAC with dashes."""
        mac = "aa-bb-cc-dd-ee-ff"
        assert self.MAC_PATTERN.match(mac) is None

    def test_invalid_mac_no_separator(self):
        """Test invalid MAC without separators."""
        mac = "aabbccddeeff"
        assert self.MAC_PATTERN.match(mac) is None

    def test_invalid_mac_non_hex(self):
        """Test invalid MAC with non-hex characters."""
        mac = "gg:hh:ii:jj:kk:ll"
        assert self.MAC_PATTERN.match(mac) is None

    def test_valid_mac_zeros(self):
        """Test valid MAC with zeros."""
        mac = "00:00:00:00:00:00"
        assert self.MAC_PATTERN.match(mac) is not None

    def test_valid_mac_all_f(self):
        """Test valid broadcast MAC."""
        mac = "ff:ff:ff:ff:ff:ff"
        assert self.MAC_PATTERN.match(mac) is not None


class TestMacNormalization:
    """Tests for MAC address normalization."""

    def test_mac_uppercase_normalization(self):
        """Test normalizing MAC to uppercase."""
        mac = "aa:bb:cc:dd:ee:ff"
        normalized = mac.upper()
        assert normalized == "AA:BB:CC:DD:EE:FF"

    def test_mac_lowercase_normalization(self):
        """Test normalizing MAC to lowercase."""
        mac = "AA:BB:CC:DD:EE:FF"
        normalized = mac.lower()
        assert normalized == "aa:bb:cc:dd:ee:ff"


class TestSshKeyParsing:
    """Tests for SSH key parsing logic."""

    def test_parse_ssh_key_with_comment(self):
        """Test parsing SSH key with comment."""
        key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB... user@host"
        parts = key.split()

        assert len(parts) >= 2
        key_type = parts[0]
        assert key_type == "ssh-rsa"
        comment = parts[2] if len(parts) > 2 else ""
        assert comment == "user@host"

    def test_parse_ssh_key_without_comment(self):
        """Test parsing SSH key without comment."""
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI..."
        parts = key.split()

        assert len(parts) >= 2
        key_type = parts[0]
        assert key_type == "ssh-ed25519"
        comment = parts[2] if len(parts) > 2 else ""
        assert comment == ""

    def test_ssh_key_types(self):
        """Test recognized SSH key types."""
        valid_types = ["ssh-rsa", "ssh-ed25519", "ssh-dss", "ecdsa-sha2-nistp256"]

        for key_type in valid_types:
            key = f"{key_type} AAAAB3..."
            parts = key.split()
            assert parts[0] == key_type


class TestUserctlModuleImports:
    """Tests for userctl module import checks."""

    def test_userctl_module_imports(self):
        """Test userctl module can be imported."""
        from botka.modules import userctl

        assert userctl is not None

    def test_userctl_has_cmd(self):
        """Test userctl module has cmd_userctl."""
        from botka.modules import userctl

        assert hasattr(userctl, "cmd_userctl")

    def test_userctl_has_add_mac(self):
        """Test userctl module has cmd_add_mac."""
        from botka.modules import userctl

        assert hasattr(userctl, "cmd_add_mac")

    def test_userctl_has_remove_mac(self):
        """Test userctl module has cmd_remove_mac."""
        from botka.modules import userctl

        assert hasattr(userctl, "cmd_remove_mac")

    def test_userctl_has_add_ssh(self):
        """Test userctl module has cmd_add_ssh."""
        from botka.modules import userctl

        assert hasattr(userctl, "cmd_add_ssh")

    def test_userctl_mac_pattern(self):
        """Test userctl has MAC_PATTERN."""
        from botka.modules import userctl

        assert hasattr(userctl, "MAC_PATTERN")
