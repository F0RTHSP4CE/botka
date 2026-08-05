from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import InputRichMessage, Message
from dishka.integrations.aiogram import FromDishka, inject

from botka.db.models import User
from botka.handlers.user_links import format_user_link
from botka.services.user_service import UserService
from botka.services.visit_service import (
    DeliveryReport,
    VisitService,
    build_cancel_notification,
    build_plan_notification,
    deliver_html_message,
)

router = Router(name=__name__)

VISIT_HELP_TEXT = """# Visit: manage visits to F0

Use `/visit` to plan your own visit or track visits of other people.

## Planning a visit

Syntax: `/visit <description>`

Examples:

* `/visit at 21:00 to go to the resident meeting`
* `/visit in 2h to drink some water (15m)`

To cancel your visit, type `/visit cancel`.

## Tracking other people’s visits

Use the `track` subcommand to track visits of other people.

Syntax: `/visit track (<handle> | <numeric ID>)`

Examples:

* `/visit track @alurm`
* `/visit track 322363419`

You’ll receive a message when someone you track plans or cancels a visit. You’ll also be notified when they arrive at F0, provided they have registered a device that connects to F0’s network.

To stop tracking someone, type `/visit untrack (<handle> | <numeric ID>)`.

Examples:

* `/visit untrack @alurm`
* `/visit untrack 322363419`

Type `/visit trackers` to see who tracks your visits, and type `/visit tracking` to see whose visits you track.
"""

# Keep HTML delivery reports comfortably below Telegram's message-length limit.
_MAX_REPORT_USERS = 100


@router.message(Command("visit"))
@inject
async def visit_handler(
    message: Message,
    command: CommandObject,
    user_service: FromDishka[UserService],
    visit_service: FromDishka[VisitService],
    user_record: User | None = None,
) -> None:
    """Dispatch reserved subcommands and treat all other arguments as a plan."""
    if user_record is None:
        await message.reply("Could not load your user record.")
        return

    args = (command.args or "").strip()
    if not args:
        await visit_service.record_event(user_record.id, "help")
        await message.reply_rich(InputRichMessage(markdown=VISIT_HELP_TEXT))
        return

    # Commands that need arguments reserve their first word. Everything else is
    # an opaque visit description.
    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower()
    remainder = parts[1].strip() if len(parts) == 2 else ""

    if subcommand == "cancel" and not remainder:
        await _cancel(message, user_record, visit_service)
        return
    if subcommand == "track":
        await _change_tracking(
            message,
            "track",
            remainder,
            user_record,
            user_service,
            visit_service,
        )
        return
    if subcommand == "untrack":
        await _change_tracking(
            message,
            "untrack",
            remainder,
            user_record,
            user_service,
            visit_service,
        )
        return
    if subcommand == "trackers" and not remainder:
        await _list_tracking(message, "trackers", user_record, visit_service)
        return
    if subcommand == "tracking" and not remainder:
        await _list_tracking(message, "tracking", user_record, visit_service)
        return

    await _plan(message, args, user_record, visit_service)


async def _plan(
    message: Message,
    description: str,
    planner: User,
    visit_service: VisitService,
) -> None:
    """Record a plan call and announce it to the planner's current trackers."""
    if not description:
        await message.reply("Usage: /visit <description>")
        return
    bot = message.bot
    if bot is None:
        await message.reply("Could not access the bot client.")
        return
    await visit_service.record_event(planner.id, "plan")
    trackers = await visit_service.list_trackers(planner.id)
    report = await deliver_html_message(
        bot,
        trackers,
        build_plan_notification(planner, description),
        description="visit plan notification",
    )
    await _reply_with_delivery_report(
        message,
        report,
        success_text="Visit announced.",
        partial_text="Visit announced to some trackers.",
        failure_text="Your visit announcement couldn’t be delivered.",
    )


async def _cancel(
    message: Message,
    planner: User,
    visit_service: VisitService,
) -> None:
    """Record and broadcast a cancellation without requiring stored plan state."""
    bot = message.bot
    if bot is None:
        await message.reply("Could not access the bot client.")
        return
    await visit_service.record_event(planner.id, "cancel")
    trackers = await visit_service.list_trackers(planner.id)
    report = await deliver_html_message(
        bot,
        trackers,
        build_cancel_notification(planner),
        description="visit cancellation notification",
    )
    await _reply_with_delivery_report(
        message,
        report,
        success_text="Visit cancellation announced.",
        partial_text="Visit cancellation announced to some trackers.",
        failure_text="Your visit cancellation couldn’t be delivered.",
    )


async def _change_tracking(
    message: Message,
    action: Literal["track", "untrack"],
    target_text: str,
    actor: User,
    user_service: UserService,
    visit_service: VisitService,
) -> None:
    """Resolve a target and add or remove the actor's tracking relationship."""
    if not target_text or len(target_text.split()) != 1:
        await message.reply(f"Usage: /visit {action} (<handle> | <numeric ID>)")
        return
    target = await _resolve_target(target_text, user_service)
    if target is None:
        await message.reply(
            "User not found. They need to have interacted with Botka first."
        )
        return

    if action == "track":
        created = await visit_service.track(actor.id, target.id)
        await visit_service.record_event(actor.id, "track")
        prefix = "You now track " if created else "You already track "
    else:
        removed = await visit_service.untrack(actor.id, target.id)
        await visit_service.record_event(actor.id, "untrack")
        prefix = "You no longer track " if removed else "You weren’t tracking "
    await message.reply(
        f"{prefix}"
        f"{format_user_link(telegram_id=target.telegram_id, username=target.username)}.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _list_tracking(
    message: Message,
    action: Literal["trackers", "tracking"],
    actor: User,
    visit_service: VisitService,
) -> None:
    """Show either who tracks the actor or whose visits the actor tracks."""
    if action == "trackers":
        users = await visit_service.list_trackers(actor.id)
        await visit_service.record_event(actor.id, "trackers")
        if not users:
            await message.reply("No one tracks your visits.")
            return
        heading = "Your visits are tracked by:"
    else:
        users = await visit_service.list_tracking(actor.id)
        await visit_service.record_event(actor.id, "tracking")
        if not users:
            await message.reply("You aren’t tracking anyone.")
            return
        heading = "You track visits of:"
    await message.reply(
        _format_user_list(heading, users),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _resolve_target(target: str, user_service: UserService) -> User | None:
    """Resolve an @handle or numeric Telegram ID to a known Botka user."""
    if target.startswith("@") and len(target) > 1:
        return await user_service.get_user_by_username(target)
    if target.isdecimal():
        return await user_service.get_user(int(target))
    return None


def _format_user_list(heading: str, users: Sequence[User]) -> str:
    links = [
        format_user_link(telegram_id=user.telegram_id, username=user.username)
        for user in users
    ]
    return "\n".join([heading, *(f"• {link}" for link in links)])


async def _reply_with_delivery_report(
    message: Message,
    report: DeliveryReport,
    *,
    success_text: str,
    partial_text: str,
    failure_text: str,
) -> None:
    """Report successful and failed DMs, splitting unusually large reports."""
    if not report.notified and not report.failed:
        await message.reply(
            "No one is tracking your visits, so no notifications were sent."
        )
        return

    if report.notified and report.failed:
        summary = partial_text
    elif report.notified:
        summary = success_text
    else:
        summary = failure_text

    users = [("Notified", report.notified), ("Couldn’t notify", report.failed)]
    total_users = len(report.notified) + len(report.failed)
    if total_users <= _MAX_REPORT_USERS:
        lines = [summary]
        for prefix, section_users in users:
            if section_users:
                lines.append(_format_user_report_line(prefix, section_users))
        await message.reply(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    await message.reply(summary)
    for prefix, section_users in users:
        for start in range(0, len(section_users), _MAX_REPORT_USERS):
            chunk = section_users[start : start + _MAX_REPORT_USERS]
            await message.answer(
                _format_user_report_line(prefix, chunk),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


def _format_user_report_line(prefix: str, users: Sequence[User]) -> str:
    links = ", ".join(
        format_user_link(telegram_id=user.telegram_id, username=user.username)
        for user in users
    )
    return f"{prefix} {links}."
