from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import UserTier
from botka.handlers.doors.utils import (
    DOOR_BOTH_ID,
    DOOR_GATE_ID,
    DOOR_MAIN_ID,
    door_label,
)
from botka.services.usbutler_service import UsbutlerService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.callback_query(F.data.startswith("door_open:"))
@inject
async def open_door_callback(
    callback: CallbackQuery,
    usbutler_service: FromDishka[UsbutlerService],
    user_service: FromDishka[UserService],
) -> None:
    if callback.from_user is None:
        await callback.answer("Unknown user.", show_alert=True)
        return
    if callback.data is None:
        await callback.answer("Invalid request.", show_alert=True)
        return
    try:
        door_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid door.", show_alert=True)
        return
    tier = await user_service.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
    )
    if door_id in (DOOR_MAIN_ID, DOOR_BOTH_ID) and tier not in (
        UserTier.resident,
        UserTier.member,
    ):
        await callback.answer(
            "Only residents and members can open the main door.",
            show_alert=True,
        )
        return
    if not usbutler_service.is_configured:
        await callback.answer("Door service not configured.", show_alert=True)
        if callback.message is not None:
            await callback.message.edit_text(
                "Door service is not configured.",
                reply_markup=None,
            )
        return
    username = callback.from_user.username or str(callback.from_user.id)
    if door_id == DOOR_BOTH_ID:
        gate_opened = await usbutler_service.open_door(DOOR_GATE_ID, username)
        main_opened = await usbutler_service.open_door(DOOR_MAIN_ID, username)
        label = door_label(door_id)
        if main_opened and gate_opened:
            opened = True
            text = f"✅ {label.capitalize()} opened."
        else:
            opened = False
            status_parts = []
            status_parts.append(
                "main door: opened" if main_opened else "main door: failed"
            )
            status_parts.append("gate: opened" if gate_opened else "gate: failed")
            status = ", ".join(status_parts)
            text = f"❌ Could not open all doors ({status})."
    else:
        opened = await usbutler_service.open_door(door_id, username)
        label = door_label(door_id)
        if opened:
            text = f"✅ {label.capitalize()} opened."
        else:
            text = f"❌ Failed to open the {label}."
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=None)
    await callback.answer(
        "Door opened." if opened else "Failed to open door.",
        show_alert=not opened,
    )
