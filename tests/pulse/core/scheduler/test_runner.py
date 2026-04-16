from __future__ import annotations

import asyncio

from pulse.core.scheduler import BackgroundSchedulerRunner, ScheduleTask


def test_background_runner_run_once_updates_status() -> None:
    called: list[str] = []
    runner = BackgroundSchedulerRunner(tick_seconds=1)
    runner.register(
        ScheduleTask(
            name="job",
            interval_seconds=60,
            run_immediately=True,
            handler=lambda: called.append("run"),
        )
    )

    ran_tasks = asyncio.run(runner.run_once())
    assert ran_tasks == ["job"]
    assert called == ["run"]
    status = runner.status()
    assert status["last_ran_tasks"] == ["job"]
    assert status["last_error"] is None


def test_background_runner_start_stop_cycle() -> None:
    runner = BackgroundSchedulerRunner(tick_seconds=1)
    runner.register(
        ScheduleTask(
            name="job",
            interval_seconds=300,
            run_immediately=False,
            handler=lambda: None,
        )
    )
    assert runner.start() is True
    assert runner.start() is False
    assert runner.stop() is True
