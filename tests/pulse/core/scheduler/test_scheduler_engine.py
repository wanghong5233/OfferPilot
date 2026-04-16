from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from pulse.core.scheduler.engine import ScheduleTask, SchedulerEngine


def test_schedule_task_validates_interval() -> None:
    try:
        ScheduleTask(name="x", interval_seconds=0, handler=lambda: None)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_scheduler_runs_due_task_with_interval() -> None:
    called: list[str] = []
    engine = SchedulerEngine()
    engine.register(
        ScheduleTask(
            name="job",
            interval_seconds=60,
            run_immediately=True,
            handler=lambda: called.append("run"),
        )
    )

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ran_first = asyncio.run(engine.run_pending(now=t0))
    assert ran_first == ["job"]
    assert called == ["run"]

    ran_second = asyncio.run(engine.run_pending(now=t0 + timedelta(seconds=30)))
    assert ran_second == []

    ran_third = asyncio.run(engine.run_pending(now=t0 + timedelta(seconds=60)))
    assert ran_third == ["job"]
    assert called == ["run", "run"]
