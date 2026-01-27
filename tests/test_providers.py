"""Tests for dependency injection providers."""

import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from dishka import make_async_container, Scope

from botka.providers import (
    ConfigProvider,
    HttpClientProvider,
    DatabaseProvider,
    SessionProvider,
    StateProvider,
    ServiceProvider,
    ActiveUsers,
    create_providers,
)
from botka.config import Config, TelegramConfig


class TestConfigProvider:
    """Tests for ConfigProvider."""
    
    def test_create_provider(self):
        """Test creating config provider."""
        provider = ConfigProvider()
        assert provider is not None
    
    def test_create_provider_with_path(self):
        """Test creating config provider with custom path."""
        provider = ConfigProvider(config_path="/custom/path.yaml")
        assert provider._config_path == "/custom/path.yaml"


class TestStateProvider:
    """Tests for StateProvider."""
    
    def test_create_state_provider(self):
        """Test creating state provider."""
        provider = StateProvider()
        assert provider._active_users == set()
    
    def test_update_active_users(self):
        """Test updating active users."""
        provider = StateProvider()
        
        provider.update_active_users({1, 2, 3})
        assert provider._active_users == {1, 2, 3}
        
        provider.update_active_users({4, 5})
        assert provider._active_users == {4, 5}
    
    def test_update_active_users_clears_old(self):
        """Test that updating clears old users."""
        provider = StateProvider()
        
        provider.update_active_users({1, 2, 3})
        provider.update_active_users(set())
        
        assert provider._active_users == set()


class TestDatabaseProvider:
    """Tests for DatabaseProvider."""
    
    def test_create_with_default_path(self):
        """Test creating with default database path."""
        provider = DatabaseProvider()
        assert provider._db_path == "db.sqlite3"
    
    def test_create_with_custom_path(self):
        """Test creating with custom database path."""
        provider = DatabaseProvider(db_path="/data/bot.db")
        assert provider._db_path == "/data/bot.db"


class TestCreateProviders:
    """Tests for create_providers function."""
    
    def test_creates_all_providers(self):
        """Test that all providers are created."""
        providers = create_providers()
        
        assert len(providers) == 6
        assert isinstance(providers[0], ConfigProvider)
        assert isinstance(providers[1], HttpClientProvider)
        assert isinstance(providers[2], DatabaseProvider)
        assert isinstance(providers[3], SessionProvider)
        assert isinstance(providers[4], StateProvider)
        assert isinstance(providers[5], ServiceProvider)
    
    def test_passes_config_path(self):
        """Test config path is passed to provider."""
        providers = create_providers(config_path="/my/config.yaml")
        config_provider = providers[0]
        assert config_provider._config_path == "/my/config.yaml"
    
    def test_passes_db_path(self):
        """Test db path is passed to provider."""
        providers = create_providers(db_path="/my/db.sqlite")
        db_provider = providers[2]
        assert db_provider._db_path == "/my/db.sqlite"


class TestActiveUsersType:
    """Tests for ActiveUsers NewType."""
    
    def test_active_users_is_set(self):
        """Test ActiveUsers wraps a set."""
        users = ActiveUsers({1, 2, 3})
        assert isinstance(users, set)
        assert 1 in users
        assert 4 not in users
    
    def test_active_users_empty(self):
        """Test empty ActiveUsers."""
        users = ActiveUsers(set())
        assert len(users) == 0


class TestProviderScopes:
    """Tests for provider scope configuration."""
    
    def test_config_provider_app_scope(self):
        """Test ConfigProvider has APP scope."""
        provider = ConfigProvider()
        assert provider.scope == Scope.APP
    
    def test_http_provider_app_scope(self):
        """Test HttpClientProvider has APP scope."""
        provider = HttpClientProvider()
        assert provider.scope == Scope.APP
    
    def test_database_provider_app_scope(self):
        """Test DatabaseProvider has APP scope."""
        provider = DatabaseProvider()
        assert provider.scope == Scope.APP
    
    def test_session_provider_request_scope(self):
        """Test SessionProvider has REQUEST scope."""
        provider = SessionProvider()
        assert provider.scope == Scope.REQUEST
    
    def test_state_provider_app_scope(self):
        """Test StateProvider has APP scope."""
        provider = StateProvider()
        assert provider.scope == Scope.APP
    
    def test_service_provider_request_scope(self):
        """Test ServiceProvider has REQUEST scope."""
        provider = ServiceProvider()
        assert provider.scope == Scope.REQUEST
