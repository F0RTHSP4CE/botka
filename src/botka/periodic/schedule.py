from __future__ import annotations

from collections.abc import Sequence

from botka.config import Settings
from botka.periodic.jobs import (
    PeriodicJob,
    poll_maintenance,
    send_good_morning,
    send_heartbeat,
)


def build_schedule(settings: Settings) -> Sequence[PeriodicJob]:
    jobs: list[PeriodicJob] = []
    interval = settings.periodic_heartbeat_seconds
    if interval > 0:
        jobs.append(
            PeriodicJob(
                name="heartbeat",
                handler=send_heartbeat,
                interval_seconds=interval,
            )
        )
    poll_interval = settings.polls_maintenance_interval_seconds
    if poll_interval > 0:
        jobs.append(
            PeriodicJob(
                name="poll_maintenance",
                handler=poll_maintenance,
                interval_seconds=poll_interval,
            )
        )
    jobs.append(
        PeriodicJob(
            name="good_morning",
            handler=send_good_morning,
            cron_hour=11,
            cron_minute=00,
        )
    )
    return jobs


def get_job(settings: Settings, name: str) -> PeriodicJob | None:
    for job in build_schedule(settings):
        if job.name == name:
            return job
    return None
