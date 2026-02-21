#!/usr/bin/env python3
"""Print all Planka boards and their lists with IDs.

Usage:
    uv run scripts/planka_lists.py
    # or with explicit credentials:
    BOTKA_PLANKA_BASE_URL=http://... BOTKA_PLANKA_USERNAME_OR_EMAIL=... \
        BOTKA_PLANKA_PASSWORD=... uv run scripts/planka_lists.py
"""
from __future__ import annotations

import asyncio
import sys

from botka.config import Settings
from botka.services.planka_client import PlankaClient


async def main() -> None:
    settings = Settings()

    if not settings.planka_base_url:
        sys.exit("BOTKA_PLANKA_BASE_URL is not set")
    if not settings.planka_username_or_email or not settings.planka_password:
        sys.exit("BOTKA_PLANKA_USERNAME_OR_EMAIL / BOTKA_PLANKA_PASSWORD are not set")

    client = PlankaClient(
        base_url=settings.planka_base_url,
        username_or_email=settings.planka_username_or_email,
        password=settings.planka_password,
        timeout_seconds=settings.planka_request_timeout_seconds,
    )
    await client.start()

    try:
        boards = await client.list_boards()
        if not boards:
            print("No boards found.")
            return

        for board in boards:
            print(f"\nBoard: {board.name!r}  id={board.id}")
            lists = await client.get_board_lists(board.id)
            if not lists:
                print("  (no lists)")
            for lst in lists:
                print(f"  List: {lst.name!r}  id={lst.id}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
