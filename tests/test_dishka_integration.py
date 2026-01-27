"""Tests for Dishka integration with python-telegram-bot."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from botka.dishka_integration import (
    setup_dishka,
    get_container,
    inject,
    FromDishka,
    CONTAINER_KEY,
)


class TestSetupDishka:
    """Tests for setup_dishka function."""
    
    def test_setup_stores_container(self):
        """Test that setup stores container in bot_data."""
        mock_container = MagicMock()
        mock_app = MagicMock()
        mock_app.bot_data = {}
        
        setup_dishka(mock_container, mock_app)
        
        assert mock_app.bot_data[CONTAINER_KEY] is mock_container


class TestGetContainer:
    """Tests for get_container function."""
    
    def test_get_container_returns_container(self):
        """Test getting container from context."""
        mock_container = MagicMock()
        mock_context = MagicMock()
        mock_context.bot_data = {CONTAINER_KEY: mock_container}
        
        result = get_container(mock_context)
        
        assert result is mock_container
    
    def test_get_container_raises_if_not_setup(self):
        """Test that getting container raises if not set up."""
        mock_context = MagicMock()
        mock_context.bot_data = {}
        
        with pytest.raises(RuntimeError, match="Dishka container not found"):
            get_container(mock_context)


class TestFromDishka:
    """Tests for FromDishka type hint."""
    
    def test_from_dishka_returns_inner_type(self):
        """Test that FromDishka[Type] returns the inner type."""
        result = FromDishka[str]
        assert result is str
    
    def test_from_dishka_with_custom_class(self):
        """Test FromDishka with custom class."""
        class MyService:
            pass
        
        result = FromDishka[MyService]
        assert result is MyService


class TestInjectDecorator:
    """Tests for inject decorator."""
    
    def test_inject_preserves_function_name(self):
        """Test that inject preserves function name."""
        @inject
        async def my_handler(update, context):
            pass
        
        assert my_handler.__name__ == "my_handler"
    
    def test_inject_preserves_docstring(self):
        """Test that inject preserves docstring."""
        @inject
        async def my_handler(update, context):
            """My docstring."""
            pass
        
        assert my_handler.__doc__ == "My docstring."


class TestContainerKey:
    """Tests for CONTAINER_KEY constant."""
    
    def test_container_key_is_string(self):
        """Test container key is a string."""
        assert isinstance(CONTAINER_KEY, str)
    
    def test_container_key_is_descriptive(self):
        """Test container key contains dishka."""
        assert "dishka" in CONTAINER_KEY
