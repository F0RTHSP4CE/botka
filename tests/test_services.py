"""Tests for services module."""

import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from botka.services import (
    parse_mikrotik_duration,
    get_mikrotik_leases,
    get_camera_image,
    open_door,
    get_wikijs_page,
    Lease,
)
from botka.config import MikrotikConfig, EspCamConfig


class TestParseMikrotikDuration:
    """Tests for Mikrotik duration parsing."""

    def test_parse_seconds(self):
        """Test parsing seconds only."""
        result = parse_mikrotik_duration("30s")
        assert result == timedelta(seconds=30)

    def test_parse_minutes(self):
        """Test parsing minutes only."""
        result = parse_mikrotik_duration("5m")
        assert result == timedelta(minutes=5)

    def test_parse_hours(self):
        """Test parsing hours only."""
        result = parse_mikrotik_duration("2h")
        assert result == timedelta(hours=2)

    def test_parse_days(self):
        """Test parsing days only."""
        result = parse_mikrotik_duration("1d")
        assert result == timedelta(days=1)

    def test_parse_combined(self):
        """Test parsing combined duration."""
        result = parse_mikrotik_duration("1d2h3m4s")
        expected = timedelta(days=1, hours=2, minutes=3, seconds=4)
        assert result == expected

    def test_parse_partial(self):
        """Test parsing partial duration."""
        result = parse_mikrotik_duration("5m30s")
        assert result == timedelta(minutes=5, seconds=30)

    def test_parse_zero(self):
        """Test parsing zero duration."""
        result = parse_mikrotik_duration("0s")
        assert result == timedelta(seconds=0)


class TestGetMikrotikLeases:
    """Tests for Mikrotik lease fetching."""

    @pytest.mark.asyncio
    async def test_get_leases_success(self, mock_http_client):
        """Test successful lease fetching."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"mac-address": "AA:BB:CC:DD:EE:FF", "last-seen": "5m30s"},
            {"mac-address": "11:22:33:44:55:66", "last-seen": "1h"},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        config = MikrotikConfig(
            host="10.0.0.1",
            username="admin",
            password="secret",
            scheme="http",
        )

        leases = await get_mikrotik_leases(mock_http_client, config)

        assert len(leases) == 2
        assert leases[0].mac_address == "AA:BB:CC:DD:EE:FF"
        assert leases[0].last_seen == timedelta(minutes=5, seconds=30)
        assert leases[1].mac_address == "11:22:33:44:55:66"
        assert leases[1].last_seen == timedelta(hours=1)

    @pytest.mark.asyncio
    async def test_get_leases_auto_scheme_fallback(self, mock_http_client):
        """Test auto scheme falls back to http on https failure."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"mac-address": "AA:BB:CC:DD:EE:FF", "last-seen": "0s"},
        ]
        mock_response.raise_for_status = MagicMock()

        # First call (https) fails, second (http) succeeds
        mock_http_client.post.side_effect = [
            Exception("HTTPS failed"),
            mock_response,
        ]

        config = MikrotikConfig(
            host="10.0.0.1",
            username="admin",
            password="secret",
            scheme="auto",
        )

        leases = await get_mikrotik_leases(mock_http_client, config)
        assert len(leases) == 1


class TestGetCameraImage:
    """Tests for camera image fetching."""

    @pytest.mark.asyncio
    async def test_get_image_success(self, mock_http_client):
        """Test successful image fetching."""
        mock_response = MagicMock()
        mock_response.content = b"fake_image_data"
        mock_response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = mock_response

        config = EspCamConfig(url="http://camera.local/")

        image = await get_camera_image(mock_http_client, config)

        assert image == b"fake_image_data"
        mock_http_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_image_failure(self, mock_http_client):
        """Test image fetching failure returns None."""
        mock_http_client.get.side_effect = Exception("Connection failed")

        config = EspCamConfig(url="http://camera.local/")

        image = await get_camera_image(mock_http_client, config)

        assert image is None


class TestOpenDoor:
    """Tests for door opening."""

    @pytest.mark.asyncio
    async def test_open_door_success(self, mock_http_client):
        """Test successful door opening."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        result = await open_door(
            mock_http_client,
            "http://butler.local/control",
            "secret_token",
        )

        assert result is True
        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_door_failure(self, mock_http_client):
        """Test door opening failure returns False."""
        mock_http_client.post.side_effect = Exception("Connection failed")

        result = await open_door(
            mock_http_client,
            "http://butler.local/control",
            "secret_token",
        )

        assert result is False


class TestGetWikijsPage:
    """Tests for Wiki.js page fetching."""

    @pytest.mark.asyncio
    async def test_get_page_success(self, mock_http_client):
        """Test successful page fetching."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "pages": {"single": {"content": "# Welcome\nThis is the welcome page."}}
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        content = await get_wikijs_page(
            mock_http_client,
            "http://wiki.local/graphql",
            "wiki_token",
            "/en/welcome",
        )

        assert content == "# Welcome\nThis is the welcome page."

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, mock_http_client):
        """Test page not found returns None."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"pages": {"single": None}}}
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        content = await get_wikijs_page(
            mock_http_client,
            "http://wiki.local/graphql",
            "wiki_token",
            "/en/nonexistent",
        )

        assert content is None

    @pytest.mark.asyncio
    async def test_get_page_error(self, mock_http_client):
        """Test page fetch error returns None."""
        mock_http_client.post.side_effect = Exception("GraphQL error")

        content = await get_wikijs_page(
            mock_http_client,
            "http://wiki.local/graphql",
            "wiki_token",
            "/en/page",
        )

        assert content is None
