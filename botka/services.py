"""Utility functions for external services."""

import logging
from datetime import timedelta
from typing import Optional

import httpx

from .config import MikrotikConfig, EspCamConfig

logger = logging.getLogger(__name__)


class Lease:
    """DHCP Lease from Mikrotik."""

    def __init__(self, mac_address: str, last_seen: timedelta):
        self.mac_address = mac_address
        self.last_seen = last_seen


def parse_mikrotik_duration(duration_str: str) -> timedelta:
    """Parse Mikrotik duration string like '1d2h3m4s' or '5m30s'."""
    total_seconds = 0
    current_num = ""

    for char in duration_str:
        if char.isdigit():
            current_num += char
        elif char == "d":
            total_seconds += int(current_num or 0) * 86400
            current_num = ""
        elif char == "h":
            total_seconds += int(current_num or 0) * 3600
            current_num = ""
        elif char == "m":
            total_seconds += int(current_num or 0) * 60
            current_num = ""
        elif char == "s":
            total_seconds += int(current_num or 0)
            current_num = ""

    return timedelta(seconds=total_seconds)


async def get_mikrotik_leases(
    client: httpx.AsyncClient,
    config: MikrotikConfig,
) -> list[Lease]:
    """Get DHCP leases from Mikrotik router."""

    async def attempt(scheme: str) -> list[Lease]:
        url = f"{scheme}://{config.host}/rest/ip/dhcp-server/lease/print"
        response = await client.post(
            url,
            auth=(config.username, config.password),
            json={".proplist": ["mac-address", "last-seen"]},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()

        leases = []
        for item in data:
            mac = item.get("mac-address", "")
            last_seen_str = item.get("last-seen", "0s")
            if mac:
                last_seen = parse_mikrotik_duration(last_seen_str)
                leases.append(Lease(mac_address=mac, last_seen=last_seen))
        return leases

    if config.scheme == "auto":
        try:
            return await attempt("https")
        except Exception:
            return await attempt("http")
    else:
        return await attempt(config.scheme)


async def get_camera_image(
    client: httpx.AsyncClient,
    config: EspCamConfig,
) -> Optional[bytes]:
    """Fetch image from ESP camera."""
    try:
        response = await client.get(config.url, timeout=10.0)
        response.raise_for_status()
        return response.content
    except Exception as e:
        logger.error(f"Failed to fetch camera image: {e}")
        return None


async def open_door(
    client: httpx.AsyncClient,
    url: str,
    token: str,
) -> bool:
    """Send door open command to Butler."""
    try:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "open"},
            timeout=5.0,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to open door: {e}")
        return False


async def get_wikijs_page(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    page_path: str,
) -> Optional[str]:
    """Fetch page content from Wiki.js GraphQL API."""
    query = """
    query ($path: String!) {
        pages {
            single(path: $path) {
                content
            }
        }
    }
    """
    try:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "variables": {"path": page_path},
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        page = data.get("data", {}).get("pages", {}).get("single")
        if page:
            return page.get("content")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch Wiki.js page: {e}")
        return None
