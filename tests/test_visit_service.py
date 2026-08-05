from __future__ import annotations

from sqlalchemy import select

from botka.db.models import UserTier, VisitEvent
from botka.services.user_service import UserService
from botka.services.visit_service import VisitService


async def _user(user_service: UserService, telegram_id: int, username: str | None):
    await user_service.ensure_user(telegram_id, username)
    user = await user_service.get_user(telegram_id)
    assert user is not None
    return user


async def test_visit_tracking_supports_duplicates_self_tracking_and_untracking(
    session, settings
) -> None:
    users = UserService(session, settings)
    visits = VisitService(session)
    alice = await _user(users, 100, "alice")
    bob = await _user(users, 200, "bob")
    alice.tier = UserTier.member
    bob.tier = UserTier.resident
    await session.commit()

    assert await visits.track(alice.id, alice.id) is True
    assert await visits.track(alice.id, alice.id) is False
    assert await visits.track(alice.id, bob.id) is True
    assert [user.id for user in await visits.list_tracking(alice.id)] == [
        alice.id,
        bob.id,
    ]
    assert [user.id for user in await visits.list_trackers(alice.id)] == [alice.id]

    assert await visits.untrack(alice.id, bob.id) is True
    assert await visits.untrack(alice.id, bob.id) is False


async def test_visit_tracking_lists_users_without_usernames_and_batches(
    session, settings
) -> None:
    users = UserService(session, settings)
    visits = VisitService(session)
    planner = await _user(users, 300, None)
    tracker = await _user(users, 400, None)
    tracker.tier = UserTier.member
    await session.commit()
    await visits.track(tracker.id, planner.id)

    assert [user.telegram_id for user in await visits.list_trackers(planner.id)] == [
        400
    ]
    assert {
        user.telegram_id
        for user in (await visits.list_trackers_for_users({planner.id}))[planner.id]
    } == {400}


async def test_visit_notifications_exclude_former_members(session, settings) -> None:
    users = UserService(session, settings)
    visits = VisitService(session)
    planner = await _user(users, 600, "planner")
    member = await _user(users, 700, "member")
    former_member = await _user(users, 800, "former")
    member.tier = UserTier.member
    former_member.tier = UserTier.guest
    await session.commit()
    await visits.track(member.id, planner.id)
    await visits.track(former_member.id, planner.id)

    assert [user.id for user in await visits.list_trackers(planner.id)] == [member.id]
    assert {
        user.id
        for user in (await visits.list_trackers_for_users({planner.id}))[planner.id]
    } == {member.id}


async def test_visit_event_is_recorded(session, settings) -> None:
    users = UserService(session, settings)
    visits = VisitService(session)
    user = await _user(users, 500, "visitor")

    await visits.record_event(user.id, "plan")

    event = (await session.execute(select(VisitEvent))).scalar_one()
    assert event.user_id == user.id
    assert event.action == "plan"
    assert event.created_at is not None
