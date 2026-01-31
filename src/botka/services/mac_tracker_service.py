from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

import httpx
import jwt
import json
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from botka.config import Settings
from botka.db.models import MacTrackerDevice, User
from botka.services.user_service import UserService
from aiogram import Bot


@dataclass(frozen=True)
class MacLease:
    mac_address: str
    ip_address: str
    assigned_at: datetime | None
    last_seen_raw: str | None


@dataclass(frozen=True)
class MacTrackerPresenceView:
    user_id: int
    mac_address: str
    last_seen_at: datetime | None


class MikrotikDhcpClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.mikrotik_base_url
        self._username = settings.mikrotik_username
        self._password = settings.mikrotik_password
        self._timeout = settings.mikrotik_timeout_seconds
        self._verify = settings.mikrotik_verify_tls

    def _is_configured(self) -> bool:
        return bool(self._base_url and self._username and self._password)

    async def list_active_leases(self) -> list[MacLease]:
        if not self._is_configured():
            return []
        base_url = self._base_url or ""
        username = self._username or ""
        password = self._password or ""
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                auth=(username, password),
                timeout=self._timeout,
                verify=self._verify,
            ) as client:
                response = await client.get("/rest/ip/dhcp-server/lease")
                response.raise_for_status()
                data = _safe_json(response)
        except httpx.HTTPError:
            return []
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(data, list):
            return []
        leases: list[MacLease] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            ip = (
                item.get("active-address")
                or item.get("address")
                or item.get("active_address")
            )
            mac = item.get("mac-address") or item.get("mac_address") or item.get("mac")
            if not ip or not mac:
                continue
            if not _is_active_lease(item):
                continue
            assigned_at = _get_lease_assigned_at(item)
            last_seen_raw = _get_lease_last_seen_raw(item)
            leases.append(
                MacLease(
                    mac_address=str(mac),
                    ip_address=str(ip),
                    assigned_at=assigned_at,
                    last_seen_raw=last_seen_raw,
                )
            )
        return leases


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        try:
            text = response.content.decode("utf-8", errors="replace")
            return json.loads(text)
        except (ValueError, UnicodeDecodeError):
            return []


def _is_active_lease(lease: dict) -> bool:
    status = str(lease.get("status") or "").lower()
    if status:
        return status == "bound"
    active = lease.get("active")
    if isinstance(active, str):
        return active.lower() == "true"
    if isinstance(active, bool):
        return active
    return lease.get("active-address") is not None


def _get_lease_assigned_at(lease: dict) -> datetime | None:
    now = datetime.now(timezone.utc)
    candidates = [
        lease.get("active-since"),
        lease.get("active_since"),
        lease.get("lease-start-time"),
        lease.get("lease_start_time"),
        lease.get("lease-started"),
        lease.get("lease_started"),
        lease.get("last-seen"),
        lease.get("last_seen"),
    ]
    for value in candidates:
        parsed = _parse_mikrotik_datetime(value, now)
        if parsed is not None:
            return parsed
    return None


def _get_lease_last_seen_raw(lease: dict) -> str | None:
    for key in ("last-seen", "last_seen", "last-seen-time", "last_seen_time"):
        value = lease.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_mikrotik_datetime(value, now: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    duration = _parse_mikrotik_duration(raw)
    if duration is None:
        return None
    return now - duration


def _parse_mikrotik_duration(value: str):
    import re

    pattern = r"^(?:(?P<weeks>\d+)w)?(?:(?P<days>\d+)d)?(?:(?P<hours>\d+):)?(?P<minutes>\d+):(?P<seconds>\d+)$"
    match = re.match(pattern, value)
    if not match:
        return None
    parts = {k: int(v) if v is not None else 0 for k, v in match.groupdict().items()}
    return timedelta(
        weeks=parts["weeks"],
        days=parts["days"],
        hours=parts["hours"],
        minutes=parts["minutes"],
        seconds=parts["seconds"],
    )


class MacTrackerService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mikrotik: MikrotikDhcpClient,
    ) -> None:
        self._session = session
        self._settings = settings
        self._mikrotik = mikrotik

    async def create_token(self, user_id: int) -> str:
        now = datetime.now(timezone.utc)
        ttl = self._settings.mac_tracker_jwt_ttl_seconds
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        }
        return jwt.encode(payload, self._jwt_secret(), algorithm="HS256")

    async def resolve_link(self, token: str, ip_address: str) -> MacLease | None:
        user_id = self.get_token_user_id(token)
        if user_id is None:
            return None
        lease = await self._find_lease_by_ip(ip_address)
        if lease is None:
            return None
        now = datetime.now(timezone.utc)
        seen_at = lease.assigned_at or now
        await self._upsert_device(user_id, lease, seen_at)
        await self._session.commit()
        return lease

    def get_token_user_id(self, token: str) -> int | None:
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret(),
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        sub = payload.get("sub")
        try:
            return int(sub)
        except (TypeError, ValueError):
            return None

    async def list_present_users(self) -> list[MacTrackerPresenceView]:
        leases = await self._mikrotik.list_active_leases()
        active_map = {lease.mac_address: lease for lease in leases}
        if not active_map:
            return []
        result = await self._session.execute(
            select(MacTrackerDevice).where(
                MacTrackerDevice.mac_address.in_(active_map.keys())
            )
        )
        devices = result.scalars().all()
        by_user: dict[int, MacTrackerPresenceView] = {}
        fallback_min = datetime.min.replace(tzinfo=timezone.utc)
        for device in devices:
            lease = active_map.get(device.mac_address)
            if lease is None:
                continue
            seen_at = lease.assigned_at or datetime.now(timezone.utc)
            current = by_user.get(device.user_id)
            current_seen = current.last_seen_at if current else None
            if current is None or (current_seen or fallback_min) < seen_at:
                by_user[device.user_id] = MacTrackerPresenceView(
                    user_id=device.user_id,
                    mac_address=lease.mac_address,
                    last_seen_at=seen_at,
                )
        return list(by_user.values())

    async def get_active_lease_seen_map(self) -> dict[str, str]:
        leases = await self._mikrotik.list_active_leases()
        return {
            lease.mac_address: lease.last_seen_raw
            for lease in leases
            if lease.last_seen_raw
        }

    async def list_user_macs(self, user_id: int) -> list[str]:
        result = await self._session.execute(
            select(MacTrackerDevice.mac_address).where(
                MacTrackerDevice.user_id == user_id
            )
        )
        return [row[0] for row in result.all()]

    async def clear_user_devices(self, user_id: int) -> None:
        await self._session.execute(
            delete(MacTrackerDevice).where(MacTrackerDevice.user_id == user_id)
        )
        await self._session.commit()

    async def sync_presence(self) -> set[int]:
        leases = await self._mikrotik.list_active_leases()
        active_map = {lease.mac_address: lease for lease in leases}
        if active_map:
            result = await self._session.execute(
                select(MacTrackerDevice).where(
                    MacTrackerDevice.mac_address.in_(active_map.keys())
                )
            )
            devices = result.scalars().all()
        else:
            devices = []
        present_users: dict[int, MacLease] = {}
        for device in devices:
            lease = active_map.get(device.mac_address)
            if lease is None:
                continue
            present_users[device.user_id] = lease
            device.last_seen_at = lease.assigned_at or datetime.now(timezone.utc)
            device.last_ip = lease.ip_address
        await self._session.commit()
        return set(present_users.keys())

    async def _find_lease_by_ip(self, ip_address: str) -> MacLease | None:
        leases = await self._mikrotik.list_active_leases()
        for lease in leases:
            if lease.ip_address == ip_address:
                return lease
        return None

    def _jwt_secret(self) -> str:
        return self._settings.mac_tracker_jwt_secret or self._settings.bot_token

    async def _upsert_device(
        self, user_id: int, lease: MacLease, seen_at: datetime
    ) -> None:
        result = await self._session.execute(
            select(MacTrackerDevice).where(
                MacTrackerDevice.user_id == user_id,
                MacTrackerDevice.mac_address == lease.mac_address,
            )
        )
        device = result.scalar_one_or_none()
        if device is None:
            device = MacTrackerDevice(
                user_id=user_id,
                mac_address=lease.mac_address,
                last_seen_at=seen_at,
                last_ip=lease.ip_address,
            )
            self._session.add(device)
        else:
            device.last_seen_at = seen_at
            device.last_ip = lease.ip_address


async def mac_tracker_poll_loop(
    bot,
    sessionmaker,
    settings: Settings,
) -> None:
    previous_present_ids: set[int] = set()
    while True:
        try:
            async with sessionmaker() as session:
                mikrotik = MikrotikDhcpClient(settings)
                service = MacTrackerService(session, settings, mikrotik)
                before = set(previous_present_ids)
                after = await service.sync_presence()
                previous_present_ids = set(after)
                user_service = UserService(session, settings)
                user_map = await _load_user_map(user_service, before | after)
                await _notify_presence_changes(
                    bot,
                    settings,
                    before=before,
                    after=after,
                    user_map=user_map,
                )
        finally:
            await _sleep(settings.mac_tracker_poll_seconds)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def _notify_presence_changes(
    bot: Bot,
    settings: Settings,
    *,
    before: set[int],
    after: set[int],
    user_map: dict[int, User],
) -> None:
    if settings.mac_tracker_notify_chat_id is None:
        return
    entered_ids = sorted(after - before)
    left_ids = sorted(before - after)
    if not entered_ids and not left_ids:
        return
    thread_id = settings.mac_tracker_notify_topic_id
    sections: list[str] = []
    if entered_ids:
        entered_labels = ", ".join(
            _format_user_label(user_id, user_map.get(user_id))
            for user_id in entered_ids
        )
        sections.append(f"➕ Entered space: {entered_labels}")
    if left_ids:
        left_labels = ", ".join(
            _format_user_label(user_id, user_map.get(user_id)) for user_id in left_ids
        )
        sections.append(f"➖ Left space: {left_labels}")
    await bot.send_message(
        chat_id=settings.mac_tracker_notify_chat_id,
        text="\n".join(sections),
        message_thread_id=thread_id,
        disable_web_page_preview=True,
        disable_notification=True,
    )


def _format_user_label(user_id: int, user: User | None) -> str:
    if user is None:
        label = html_escape(f"user {user_id}")
        return f"<span>{label}</span>"
    if user.username:
        href = f"https://t.me/{user.username}"
        display = f"@{user.username}"
    else:
        href = f"tg://user?id={user.telegram_id}"
        display = str(user.telegram_id)
    return (
        f'<a href="{html_escape(href, quote=True)}">' f"{html_escape(display)}" "</a>"
    )


async def _load_user_map(
    user_service: UserService,
    user_ids: set[int],
) -> dict[int, User]:
    if not user_ids:
        return {}
    users = await user_service.list_users_by_ids(user_ids)
    return {user.id: user for user in users}
