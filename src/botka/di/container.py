from __future__ import annotations

from typing import AsyncIterable

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from botka.config import Settings
from botka.db.session import create_engine, create_sessionmaker
from botka.services.borrowed_item_detector import BorrowedItemDetector
from botka.services.borrowed_items_service import BorrowedItemsService
from botka.services.polls_service import PollsService
from botka.services.shopping_list_service import ShoppingListService
from botka.services.user_service import UserService
from botka.services.usbutler_service import UsbutlerService


class AppProvider(Provider):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @provide(scope=Scope.APP)
    def settings(self) -> Settings:
        return self._settings

    @provide(scope=Scope.APP)
    def engine(self, settings: Settings) -> AsyncEngine:
        return create_engine(settings.database_url)

    @provide(scope=Scope.APP)
    def sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker:
        return create_sessionmaker(engine)

    @provide(scope=Scope.REQUEST)
    async def session(
        self, sessionmaker: async_sessionmaker
    ) -> AsyncIterable[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def user_service(self, session: AsyncSession, settings: Settings) -> UserService:
        return UserService(session, settings)

    @provide(scope=Scope.REQUEST)
    def shopping_service(self, session: AsyncSession) -> ShoppingListService:
        return ShoppingListService(session)

    @provide(scope=Scope.REQUEST)
    def borrowed_items_service(self, session: AsyncSession) -> BorrowedItemsService:
        return BorrowedItemsService(session)

    @provide(scope=Scope.REQUEST)
    def polls_service(self, session: AsyncSession) -> PollsService:
        return PollsService(session)

    @provide(scope=Scope.APP)
    def borrowed_item_detector(self, settings: Settings) -> BorrowedItemDetector:
        return BorrowedItemDetector(settings)

    @provide(scope=Scope.APP)
    def usbutler_service(self, settings: Settings) -> UsbutlerService:
        return UsbutlerService(settings)


def build_container(settings: Settings) -> AsyncContainer:
    return make_async_container(AppProvider(settings))
