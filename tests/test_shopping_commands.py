from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botka.handlers.shopping.commands import _do_needs
from botka.services.shopping_needs_publisher import (
    ShoppingNeedsPublication,
    ShoppingNeedsView,
)


@pytest.mark.asyncio
async def test_group_needs_links_the_canonical_message() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(type="supergroup"),
        bot=SimpleNamespace(),
        reply=AsyncMock(),
    )
    view = ShoppingNeedsView(())
    publisher = SimpleNamespace(
        has_target=True,
        load=AsyncMock(return_value=view),
        publish=AsyncMock(
            return_value=ShoppingNeedsPublication(
                True, "https://t.me/c/123/456"
            )
        ),
    )

    await _do_needs(message, SimpleNamespace(), publisher)

    publisher.publish.assert_awaited_once_with(message.bot, view)
    message.reply.assert_awaited_once_with(
        '📌 <a href="https://t.me/c/123/456">'
        "Open the pinned shopping list</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@pytest.mark.asyncio
async def test_private_needs_sends_a_fresh_unpinned_list() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        reply=AsyncMock(),
    )
    view = ShoppingNeedsView(())
    publisher = SimpleNamespace(
        load=AsyncMock(return_value=view),
        publish=AsyncMock(),
    )

    await _do_needs(message, SimpleNamespace(), publisher)

    message.reply.assert_awaited_once_with(
        "Shopping list is empty.",
        parse_mode="HTML",
        reply_markup=None,
    )
    publisher.publish.assert_not_awaited()
