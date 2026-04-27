"""Verify stage for Game workflow."""

from __future__ import annotations

from .._connectors import GameDriver
from .capture import capture_screen
from .types import TaskResult


def verify_task(driver: GameDriver, result: TaskResult) -> TaskResult:
    if result.status in {"dry_run", "skipped", "unknown_screen"}:
        return result
    screenshot = capture_screen(driver)
    result.screenshot_after_ref = screenshot.ref
    return result
