"""Execute stage for Game workflow."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .._connectors import GameDriver
from ..games import GameConfig, GameTaskConfig
from .capture import capture_screen
from .identify import _find_template
from .types import Screenshot, TaskResult

SafetyGate = Callable[[GameConfig, GameTaskConfig], dict[str, Any]]


def execute_task(
    *,
    driver: GameDriver,
    game: GameConfig,
    task: GameTaskConfig,
    action: dict[str, Any],
    screenshot: Screenshot,
    templates_root: Path,
    dry_run: bool,
    safety_gate: SafetyGate | None = None,
) -> TaskResult:
    if dry_run:
        return TaskResult(
            name=task.name,
            task_type=task.type,
            status="dry_run",
            succeeded=True,
            metadata={"action": action},
        )

    if safety_gate is not None:
        decision = safety_gate(game, task)
        if not decision.get("ok"):
            return TaskResult(
                name=task.name,
                task_type=task.type,
                status=str(decision.get("status") or "denied"),
                error=str(decision.get("error") or "policy_denied"),
                error_message=str(decision.get("error_message") or "Action denied by SafetyPlane."),
                metadata={"policy": decision},
            )

    if task.type == "tap_template":
        return _execute_tap(driver=driver, task=task, action=action)
    if task.type == "claim_chain":
        return _execute_claim_chain(
            driver=driver,
            game=game,
            task=task,
            screenshot=screenshot,
            templates_root=templates_root,
        )
    if task.type == "gacha":
        return _execute_gacha(driver=driver, task=task, action=action)
    if task.type == "swipe":
        return _execute_swipe(driver=driver, task=task)
    if task.type == "wait":
        return _execute_wait(task)
    return TaskResult(
        name=task.name,
        task_type=task.type,
        status="failed",
        error="unsupported_task_type",
        error_message=f"Unsupported task type: {task.type}",
    )


def _execute_tap(*, driver: GameDriver, task: GameTaskConfig, action: dict[str, Any]) -> TaskResult:
    if not action.get("ok"):
        return TaskResult(
            name=task.name,
            task_type=task.type,
            status="unknown_screen",
            error=str(action.get("error") or "unknown_screen"),
            error_message=str(action.get("error_message") or "Cannot identify target action."),
            metadata={"action": action},
        )
    result = driver.tap(x=int(action.get("x") or 0), y=int(action.get("y") or 0))
    return _result_from_driver(task, result, metadata={"action": action})


def _execute_claim_chain(
    *,
    driver: GameDriver,
    game: GameConfig,
    task: GameTaskConfig,
    screenshot: Screenshot,
    templates_root: Path,
) -> TaskResult:
    current = screenshot
    steps: list[dict[str, Any]] = []
    for index, template in enumerate(task.templates):
        action = _find_template(
            driver=driver,
            game=game,
            template=template,
            screenshot=current,
            templates_root=templates_root,
        )
        steps.append(action)
        if not action.get("ok"):
            return TaskResult(
                name=task.name,
                task_type=task.type,
                status="failed",
                error=f"chain_broken_at_step_{index + 1}",
                error_message=str(action.get("error_message") or "Claim chain template missing."),
                metadata={"steps": steps},
            )
        tap = driver.tap(x=int(action.get("x") or 0), y=int(action.get("y") or 0))
        if not tap.get("ok"):
            return _result_from_driver(task, tap, metadata={"steps": steps})
        if index < len(task.templates) - 1:
            current = capture_screen(driver)
    return TaskResult(
        name=task.name,
        task_type=task.type,
        status="success",
        succeeded=True,
        metadata={"steps": steps},
    )


def _execute_gacha(*, driver: GameDriver, task: GameTaskConfig, action: dict[str, Any]) -> TaskResult:
    template = str(task.params.get("template") or task.template or "").strip()
    if not template:
        return TaskResult(
            name=task.name,
            task_type=task.type,
            status="skipped",
            error="gacha_template_missing",
            error_message="gacha task requires params.template before real execution.",
            metadata={"params": dict(task.params)},
        )
    return _execute_tap(driver=driver, task=task, action=action)


def _execute_swipe(*, driver: GameDriver, task: GameTaskConfig) -> TaskResult:
    params = task.params
    result = driver.swipe(
        x1=int(params.get("x1") or 0),
        y1=int(params.get("y1") or 0),
        x2=int(params.get("x2") or 0),
        y2=int(params.get("y2") or 0),
        duration_ms=int(params.get("duration_ms") or 300),
    )
    return _result_from_driver(task, result)


def _execute_wait(task: GameTaskConfig) -> TaskResult:
    seconds = max(0.0, min(float(task.params.get("seconds") or 1.0), 30.0))
    time.sleep(seconds)
    return TaskResult(name=task.name, task_type=task.type, status="success", succeeded=True)


def _result_from_driver(
    task: GameTaskConfig,
    result: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> TaskResult:
    if result.get("ok"):
        return TaskResult(
            name=task.name,
            task_type=task.type,
            status=str(result.get("status") or "success"),
            succeeded=True,
            metadata=metadata or {},
        )
    return TaskResult(
        name=task.name,
        task_type=task.type,
        status="failed",
        error=str(result.get("error") or "driver_failed"),
        error_message=str(result.get("error_message") or "Driver command failed."),
        metadata=metadata or {},
    )
