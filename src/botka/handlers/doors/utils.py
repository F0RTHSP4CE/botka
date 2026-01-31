from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DOOR_MAIN_ID = 1
DOOR_GATE_ID = 2
DOOR_BOTH_ID = 12

DOOR_LABELS = {
    DOOR_MAIN_ID: "main door",
    DOOR_GATE_ID: "gate",
    DOOR_BOTH_ID: "main door and gate",
}


def build_open_keyboard(door_id: int) -> InlineKeyboardMarkup:
    label = DOOR_LABELS.get(door_id, "door")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Open {label}",
                    callback_data=f"door_open:{door_id}",
                )
            ]
        ]
    )


def door_label(door_id: int) -> str:
    return DOOR_LABELS.get(door_id, f"door {door_id}")
