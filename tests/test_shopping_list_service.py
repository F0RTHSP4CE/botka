from __future__ import annotations

import pytest

from botka.services.shopping_list_service import ShoppingListService


@pytest.mark.asyncio
async def test_add_and_list_items(session):
    service = ShoppingListService(session)

    await service.add_item(101, "Milk")
    await service.add_item(102, "Bread")

    items = await service.list_open_items()

    assert [item.text for item in items] == ["Milk", "Bread"]


@pytest.mark.asyncio
async def test_mark_bought_excludes_item(session):
    service = ShoppingListService(session)

    await service.add_item(201, "Coffee")
    await service.add_item(202, "Tea")
    items = await service.list_open_items()

    await service.mark_bought(items[0].id)
    remaining = await service.list_open_items()

    assert [item.text for item in remaining] == ["Tea"]


@pytest.mark.asyncio
async def test_extract_dash_items(session):
    service = ShoppingListService(session)

    text = """
- milk
- bread
not a list
 - eggs
"""
    items = service.extract_dash_items(text)

    assert items == ["milk", "bread", "eggs"]
