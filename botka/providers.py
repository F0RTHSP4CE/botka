"""Dependency injection providers using Dishka."""

from collections.abc import AsyncIterator
from typing import NewType

import httpx
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    AsyncEngine,
)

from .config import Config, load_config
from .di_services import (
    ResidentService,
    UserService,
    NeedsService,
    MikrotikService,
    CameraService,
    ButlerService,
)


# Type aliases for injected dependencies
ActiveUsers = NewType("ActiveUsers", set[int])


class ConfigProvider(Provider):
    """Provider for configuration."""

    scope = Scope.APP

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._config_path = config_path

    @provide
    def get_config(self) -> Config:
        """Provide application configuration."""
        return load_config(self._config_path)


class HttpClientProvider(Provider):
    """Provider for HTTP client."""

    scope = Scope.APP

    @provide
    async def get_http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Provide HTTP client with cleanup."""
        client = httpx.AsyncClient(verify=False)
        yield client
        await client.aclose()


class DatabaseProvider(Provider):
    """Provider for database connections."""

    scope = Scope.APP

    def __init__(self, db_path: str = "db.sqlite3"):
        super().__init__()
        self._db_path = db_path

    @provide
    def get_engine(self) -> AsyncEngine:
        """Provide async SQLAlchemy engine."""
        return create_async_engine(
            f"sqlite+aiosqlite:///{self._db_path}",
            echo=False,
        )

    @provide
    def get_session_factory(
        self, engine: AsyncEngine
    ) -> async_sessionmaker[AsyncSession]:
        """Provide session factory."""
        return async_sessionmaker(engine, expire_on_commit=False)


class SessionProvider(Provider):
    """Provider for database sessions (request scope)."""

    scope = Scope.REQUEST

    @provide
    async def get_session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        """Provide database session with automatic cleanup."""
        async with factory() as session:
            yield session


class StateProvider(Provider):
    """Provider for bot state."""

    scope = Scope.APP

    def __init__(self):
        super().__init__()
        self._active_users: set[int] = set()

    @provide
    def get_active_users(self) -> ActiveUsers:
        """Provide active users set."""
        return ActiveUsers(self._active_users)

    def update_active_users(self, users: set[int]) -> None:
        """Update active users (called from outside DI)."""
        self._active_users.clear()
        self._active_users.update(users)


class ServiceProvider(Provider):
    """Provider for application services."""

    scope = Scope.REQUEST

    @provide
    def get_resident_service(self, session: AsyncSession) -> ResidentService:
        """Provide resident service."""
        return ResidentService(session)

    @provide
    def get_user_service(self, session: AsyncSession) -> UserService:
        """Provide user service."""
        return UserService(session)

    @provide
    def get_needs_service(self, session: AsyncSession) -> NeedsService:
        """Provide needs service."""
        return NeedsService(session)

    @provide
    def get_mikrotik_service(
        self, http_client: httpx.AsyncClient, config: Config
    ) -> MikrotikService:
        """Provide Mikrotik service."""
        return MikrotikService(http_client, config.services.mikrotik)

    @provide
    def get_camera_service(
        self, http_client: httpx.AsyncClient, config: Config
    ) -> CameraService:
        """Provide camera service."""
        return CameraService(http_client, config)

    @provide
    def get_butler_service(
        self, http_client: httpx.AsyncClient, config: Config
    ) -> ButlerService:
        """Provide butler service."""
        return ButlerService(http_client, config.services.butler)


def create_providers(
    config_path: str | None = None,
    db_path: str = "db.sqlite3",
) -> tuple[
    ConfigProvider,
    HttpClientProvider,
    DatabaseProvider,
    SessionProvider,
    StateProvider,
    ServiceProvider,
]:
    """Create all providers for the application."""
    return (
        ConfigProvider(config_path),
        HttpClientProvider(),
        DatabaseProvider(db_path),
        SessionProvider(),
        StateProvider(),
        ServiceProvider(),
    )
