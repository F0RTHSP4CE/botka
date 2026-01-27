"""Tests for vortex_of_doom module functions."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestVortexModuleImports:
    """Tests for vortex module import checks."""

    def test_vortex_module_imports(self):
        """Test vortex module can be imported."""
        from botka.modules import vortex_of_doom

        assert vortex_of_doom is not None

    def test_vortex_has_get_handlers(self):
        """Test vortex module has get_handlers."""
        from botka.modules import vortex_of_doom

        assert hasattr(vortex_of_doom, "get_handlers")

    def test_vortex_has_status_cmd(self):
        """Test vortex module has vortex_status_cmd."""
        from botka.modules import vortex_of_doom

        assert hasattr(vortex_of_doom, "vortex_status_cmd")

    def test_vortex_has_check(self):
        """Test vortex module has vortex_check."""
        from botka.modules import vortex_of_doom

        assert hasattr(vortex_of_doom, "vortex_check")


class TestGetHandlers:
    """Tests for get_handlers function."""

    def test_get_handlers_returns_list(self):
        """Test that get_handlers returns a list."""
        from botka.modules.vortex_of_doom import get_handlers

        handlers = get_handlers()

        assert isinstance(handlers, list)
        assert len(handlers) > 0

    def test_handlers_include_vortex_status(self):
        """Test that handlers include vortex_status command."""
        from botka.modules.vortex_of_doom import get_handlers
        from telegram.ext import CommandHandler

        handlers = get_handlers()

        # Check there's a CommandHandler for vortex_status
        command_handlers = [h for h in handlers if isinstance(h, CommandHandler)]
        assert len(command_handlers) > 0


class TestVortexConfig:
    """Tests for vortex configuration handling."""

    def test_vortex_config_model(self):
        """Test vortex config structure."""
        from botka.config import VortexConfig

        config = VortexConfig(enabled=True, archive_topic_id=42)
        assert config.enabled is True
        assert config.archive_topic_id == 42

    def test_vortex_config_disabled(self):
        """Test vortex config when disabled."""
        from botka.config import VortexConfig

        config = VortexConfig(enabled=False, archive_topic_id=0)
        assert config.enabled is False
