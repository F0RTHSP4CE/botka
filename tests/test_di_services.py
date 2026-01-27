"""Tests for DI services."""

import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from botka.di_services import (
    ResidentService,
    UserService,
    NeedsService,
    MikrotikService,
    CameraService,
    ButlerService,
)
from botka.config import (
    MikrotikConfig,
    ButlerConfig,
    Config,
    TelegramConfig,
    ServicesConfig,
)


class TestResidentService:
    """Tests for ResidentService."""

    def test_create_service(self):
        """Test creating resident service."""
        mock_session = MagicMock()
        service = ResidentService(mock_session)
        assert service._session is mock_session


class TestUserService:
    """Tests for UserService."""

    def test_create_service(self):
        """Test creating user service."""
        mock_session = MagicMock()
        service = UserService(mock_session)
        assert service._session is mock_session


class TestNeedsService:
    """Tests for NeedsService."""

    def test_create_service(self):
        """Test creating needs service."""
        mock_session = MagicMock()
        service = NeedsService(mock_session)
        assert service._session is mock_session


class TestMikrotikService:
    """Tests for MikrotikService."""

    def test_create_unconfigured(self):
        """Test creating unconfigured service."""
        mock_client = MagicMock()
        service = MikrotikService(mock_client, None)

        assert service.is_configured is False

    def test_create_configured(self):
        """Test creating configured service."""
        mock_client = MagicMock()
        config = MikrotikConfig(
            host="192.168.88.1", username="admin", password="secret"
        )
        service = MikrotikService(mock_client, config)

        assert service.is_configured is True

    @pytest.mark.asyncio
    async def test_get_leases_unconfigured(self):
        """Test getting leases when unconfigured returns empty."""
        mock_client = MagicMock()
        service = MikrotikService(mock_client, None)

        leases = await service.get_leases()
        assert leases == []


class TestCameraService:
    """Tests for CameraService."""

    def test_create_service(self):
        """Test creating camera service."""
        mock_client = MagicMock()
        config = Config(telegram=TelegramConfig(token="test"))
        service = CameraService(mock_client, config)

        assert service._client is mock_client
        assert service._config is config


class TestButlerService:
    """Tests for ButlerService."""

    def test_create_unconfigured(self):
        """Test creating unconfigured service."""
        mock_client = MagicMock()
        service = ButlerService(mock_client, None)

        assert service.is_configured is False

    def test_create_configured(self):
        """Test creating configured service."""
        mock_client = MagicMock()
        config = ButlerConfig(url="http://door.local", token="secret")
        service = ButlerService(mock_client, config)

        assert service.is_configured is True

    @pytest.mark.asyncio
    async def test_open_door_unconfigured(self):
        """Test opening door when unconfigured returns False."""
        mock_client = MagicMock()
        service = ButlerService(mock_client, None)

        result = await service.open_door()
        assert result is False
