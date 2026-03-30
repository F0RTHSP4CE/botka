from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from botka.config import Settings

logger = logging.getLogger(__name__)

_SENSOR_NAMES = [
    "Battery Charge",
    "Battery Current",
    "Battery ETA",
    "Battery SOC Rate",
    "Battery Voltage",
    "Battery Voltage Rate",
]

_TEXT_SENSOR_NAMES = [
    "Battery State",
]


@dataclass(frozen=True)
class UpsStatus:
    battery_charge: float | None  # %
    battery_current: float | None  # A
    battery_eta: float | None  # min
    battery_soc_rate: float | None  # %/h
    battery_state: str | None  # e.g. "discharging", "charging", "idle"
    battery_voltage: float | None  # V
    battery_voltage_rate: float | None  # V/h

    @property
    def is_discharging(self) -> bool:
        return (
            self.battery_state is not None and "discharg" in self.battery_state.lower()
        )

    def format_text(self) -> str:
        lines: list[str] = []
        if self.battery_state is not None:
            state_icon = "🔴" if self.is_discharging else "🟢"
            lines.append(f"{state_icon} Battery State: <b>{self.battery_state}</b>")
        if self.battery_charge is not None:
            lines.append(f"Battery Charge: <b>{self.battery_charge:.0f}%</b>")
        if self.battery_voltage is not None:
            lines.append(f"Battery Voltage: <b>{self.battery_voltage:.2f} V</b>")
        if self.battery_current is not None:
            lines.append(f"Battery Current: <b>{self.battery_current:.2f} A</b>")
        if self.battery_eta is not None:
            lines.append(f"Battery ETA: {self.battery_eta:.0f} min")
        if self.battery_soc_rate is not None:
            lines.append(f"SOC Rate: {self.battery_soc_rate:.1f}% / h")
        if self.battery_voltage_rate is not None:
            lines.append(f"Voltage Rate: {self.battery_voltage_rate:.2f} V/h")
        return "\n".join(lines)


class UpsClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = (
            settings.ups_base_url.rstrip("/") if settings.ups_base_url else None
        )
        self._timeout = httpx.Timeout(settings.ups_timeout_seconds)

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url)

    async def get_status(self) -> UpsStatus:
        """Fetch all sensor values from the ESPHome device and return a UpsStatus."""
        sensors: dict[str, float | None] = {}
        text_sensors: dict[str, str | None] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for name in _SENSOR_NAMES:
                sensors[name] = await self._read_sensor(client, name)
            for name in _TEXT_SENSOR_NAMES:
                text_sensors[name] = await self._read_text_sensor(client, name)

        return UpsStatus(
            battery_charge=sensors.get("Battery Charge"),
            battery_current=sensors.get("Battery Current"),
            battery_eta=sensors.get("Battery ETA"),
            battery_soc_rate=sensors.get("Battery SOC Rate"),
            battery_state=text_sensors.get("Battery State"),
            battery_voltage=sensors.get("Battery Voltage"),
            battery_voltage_rate=sensors.get("Battery Voltage Rate"),
        )

    async def _read_sensor(self, client: httpx.AsyncClient, name: str) -> float | None:
        url = f"{self._base_url}/sensor/{quote(name, safe='')}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("value")
        except Exception:
            logger.warning("Failed to read sensor %s", name, exc_info=True)
            return None

    async def _read_text_sensor(
        self, client: httpx.AsyncClient, name: str
    ) -> str | None:
        url = f"{self._base_url}/text_sensor/{quote(name, safe='')}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("value")
        except Exception:
            logger.warning("Failed to read text sensor %s", name, exc_info=True)
            return None
