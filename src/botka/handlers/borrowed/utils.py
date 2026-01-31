from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_return_keyboard(
    items: Sequence[tuple[int, str, bool]],
) -> InlineKeyboardMarkup:
    use_item_labels = len(items) > 1
    rows = []
    for item_id, item_name, returned in items:
        if returned:
            label = f"☑️ Returned {item_name}" if use_item_labels else "☑️ Returned"
        else:
            label = f"Return {item_name}" if use_item_labels else "Mark returned"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"borrowed_return:{item_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
