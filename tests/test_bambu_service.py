import base64
from unittest.mock import Mock

import pytest

from botka.services import bambu_service
from botka.services.bambu_service import BambuPrinterConfig, BambuService


def _config() -> BambuPrinterConfig:
    return BambuPrinterConfig(
        name="A1",
        ip="10.0.0.10",
        serial="serial",
        access_code="code",
    )


@pytest.mark.asyncio
async def test_connect_starts_only_mqtt(monkeypatch) -> None:
    printer = Mock()
    monkeypatch.setattr(bambu_service.bl, "Printer", Mock(return_value=printer))
    service = BambuService([_config()], camera_timeout=0.01)

    await service.connect_all()

    printer.mqtt_start.assert_called_once_with()
    printer.connect.assert_not_called()
    printer.camera_start.assert_not_called()


@pytest.mark.asyncio
async def test_status_returns_offline_immediately_when_mqtt_is_not_ready(
    monkeypatch,
) -> None:
    printer = Mock()
    printer.mqtt_client_ready.return_value = False
    monkeypatch.setattr(bambu_service.bl, "Printer", Mock(return_value=printer))
    service = BambuService([_config()], camera_timeout=0.01)

    status = await service.get_status("A1")

    assert status is not None
    assert status.connected is False
    assert status.percentage is None
    printer.get_state.assert_not_called()


@pytest.mark.asyncio
async def test_camera_starts_only_when_photo_is_requested(monkeypatch) -> None:
    printer = Mock()
    printer.get_camera_frame.return_value = base64.b64encode(b"jpeg").decode()
    monkeypatch.setattr(bambu_service.bl, "Printer", Mock(return_value=printer))
    service = BambuService([_config()], camera_timeout=0.01)

    photo = await service.get_photo("A1")

    assert photo == b"jpeg"
    printer.camera_start.assert_called_once_with()
