from __future__ import annotations

from html import escape as html_escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from botka.services.mac_tracker_service import MacTrackerService
from botka.services.user_service import UserService

router = Router(name=__name__)


@router.callback_query(F.data.startswith("mac_clear:"))
@inject
async def mac_clear_callback(
    callback: CallbackQuery,
    user_service: FromDishka[UserService],
    mac_tracker: FromDishka[MacTrackerService],
) -> None:
    if callback.from_user is None:
        await callback.answer("Unknown user.", show_alert=True)
        return
    if callback.data is None:
        await callback.answer("Invalid request.", show_alert=True)
        return
    try:
        _, target_raw, actor_raw = callback.data.split(":", 2)
        target_user_id = int(target_raw)
        actor_telegram_id = int(actor_raw)
    except (ValueError, IndexError):
        await callback.answer("Invalid request.", show_alert=True)
        return
    if callback.from_user.id != actor_telegram_id:
        await callback.answer("This confirmation isn't for you.", show_alert=True)
        return
    actor_user = await user_service.get_user(actor_telegram_id)
    actor_user_id = actor_user.id if actor_user else None
    if target_user_id != actor_user_id:
        if not await user_service.is_resident(actor_telegram_id):
            await callback.answer(
                "Only residents can clear other users.", show_alert=True
            )
            return
    await mac_tracker.clear_user_devices(target_user_id)
    target_user = await user_service.get_user_by_id(target_user_id)
    if target_user is None:
        text = "✅ Cleared MAC assignments for user {}.".format(
            html_escape(str(target_user_id))
        )
    else:
        label = (
            f"@{target_user.username}"
            if target_user.username
            else str(target_user.telegram_id)
        )
        text = "✅ Cleared MAC assignments for user {}.".format(html_escape(label))
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=None)
    await callback.answer("Cleared.")
