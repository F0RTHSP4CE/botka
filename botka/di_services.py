"""Service layer with dependency injection support."""

import logging
from typing import Protocol

import httpx
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User

from .config import Config, MikrotikConfig, EspCamConfig, ButlerConfig
from .db import TgUser, Resident, UserMac, NeededItem
from .services import (
    get_mikrotik_leases as _get_mikrotik_leases,
    get_camera_image as _get_camera_image,
    open_door as _open_door,
    Lease,
)

logger = logging.getLogger(__name__)


class ResidentService:
    """Service for resident-related operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def is_resident(self, user_id: int) -> bool:
        """Check if user is a current resident."""
        result = await self._session.execute(
            select(Resident).where(
                and_(Resident.tg_id == user_id, Resident.end_date.is_(None))
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_residents(self) -> list[tuple[Resident, TgUser | None]]:
        """Get all current residents with their user info."""
        result = await self._session.execute(
            select(Resident, TgUser)
            .outerjoin(TgUser, Resident.tg_id == TgUser.id)
            .where(Resident.end_date.is_(None))
            .order_by(Resident.begin_date.desc())
        )
        return list(result.all())


class UserService:
    """Service for user-related operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_or_create_user(self, user: User) -> TgUser:
        """Get or create a TgUser record."""
        result = await self._session.execute(select(TgUser).where(TgUser.id == user.id))
        db_user = result.scalar_one_or_none()

        if db_user is None:
            db_user = TgUser(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            self._session.add(db_user)
            await self._session.commit()
        else:
            db_user.username = user.username
            db_user.first_name = user.first_name
            db_user.last_name = user.last_name
            await self._session.commit()

        return db_user

    async def get_user(self, user_id: int) -> TgUser | None:
        """Get user by ID."""
        result = await self._session.execute(select(TgUser).where(TgUser.id == user_id))
        return result.scalar_one_or_none()

    async def get_user_macs(self, user_id: int) -> list[UserMac]:
        """Get all MAC addresses for a user."""
        result = await self._session.execute(
            select(UserMac).where(UserMac.tg_id == user_id)
        )
        return list(result.scalars().all())

    async def add_mac(self, user_id: int, mac: str) -> bool:
        """Add MAC address for user. Returns True if added, False if exists."""
        result = await self._session.execute(
            select(UserMac).where(and_(UserMac.tg_id == user_id, UserMac.mac == mac))
        )
        if result.scalar_one_or_none():
            return False

        self._session.add(UserMac(tg_id=user_id, mac=mac))
        await self._session.commit()
        return True

    async def remove_mac(self, user_id: int, mac: str) -> bool:
        """Remove MAC address for user. Returns True if removed."""
        result = await self._session.execute(
            select(UserMac).where(and_(UserMac.tg_id == user_id, UserMac.mac == mac))
        )
        user_mac = result.scalar_one_or_none()
        if not user_mac:
            return False

        await self._session.delete(user_mac)
        await self._session.commit()
        return True


class NeedsService:
    """Service for shopping list operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_needs(self) -> list[tuple[NeededItem, TgUser | None]]:
        """Get all pending shopping list items."""
        result = await self._session.execute(
            select(NeededItem, TgUser)
            .outerjoin(TgUser, NeededItem.request_user_id == TgUser.id)
            .where(NeededItem.buyer_user_id.is_(None))
            .order_by(NeededItem.rowid)
        )
        return list(result.all())

    async def add_need(
        self,
        item: str,
        user_id: int,
        chat_id: int,
        message_id: int,
    ) -> NeededItem:
        """Add item to shopping list."""
        new_item = NeededItem(
            request_chat_id=chat_id,
            request_message_id=message_id,
            request_user_id=user_id,
            pinned_chat_id=chat_id,
            pinned_message_id=message_id,
            item=item,
        )
        self._session.add(new_item)
        await self._session.commit()
        return new_item

    async def mark_bought(self, item_id: int, buyer_id: int) -> bool:
        """Mark item as bought. Returns True if successful."""
        result = await self._session.execute(
            select(NeededItem).where(NeededItem.rowid == item_id)
        )
        item = result.scalar_one_or_none()

        if not item or item.buyer_user_id is not None:
            return False

        item.buyer_user_id = buyer_id
        await self._session.commit()
        return True


class MikrotikService:
    """Service for Mikrotik router operations."""

    def __init__(self, http_client: httpx.AsyncClient, config: MikrotikConfig | None):
        self._client = http_client
        self._config = config

    @property
    def is_configured(self) -> bool:
        """Check if Mikrotik is configured."""
        return self._config is not None

    async def get_leases(self) -> list[Lease]:
        """Get DHCP leases from router."""
        if not self._config:
            return []
        return await _get_mikrotik_leases(self._client, self._config)


class CameraService:
    """Service for camera operations."""

    def __init__(self, http_client: httpx.AsyncClient, config: Config):
        self._client = http_client
        self._config = config

    async def get_racovina_image(self) -> bytes | None:
        """Get image from racovina camera."""
        if not self._config.services.racovina_cam:
            return None
        return await _get_camera_image(self._client, self._config.services.racovina_cam)

    async def get_hlam_image(self) -> bytes | None:
        """Get image from hlam camera."""
        if not self._config.services.hlam_cam:
            return None
        return await _get_camera_image(self._client, self._config.services.hlam_cam)

    async def get_vortex_image(self) -> bytes | None:
        """Get image from vortex of doom camera."""
        if not self._config.services.vortex_of_doom_cam:
            return None
        return await _get_camera_image(
            self._client, self._config.services.vortex_of_doom_cam
        )


class ButlerService:
    """Service for door control operations."""

    def __init__(self, http_client: httpx.AsyncClient, config: ButlerConfig | None):
        self._client = http_client
        self._config = config

    @property
    def is_configured(self) -> bool:
        """Check if butler is configured."""
        return self._config is not None

    async def open_door(self) -> bool:
        """Open the door. Returns True if successful."""
        if not self._config:
            return False
        return await _open_door(self._client, self._config.url, self._config.token)
