"""Game driver registry."""

from __future__ import annotations

from pathlib import Path

from ..config import GameSettings
from .base import GameDriver


def build_driver(
    driver_name: str,
    *,
    settings: GameSettings,
    templates_root: Path,
) -> GameDriver:
    normalized = str(driver_name or "").strip().lower()
    if normalized == "adb_airtest":
        from .adb_airtest.driver import AdbAirtestDriver

        return AdbAirtestDriver(settings=settings, templates_root=templates_root)
    if normalized == "cloud_web":
        from .cloud_web.driver import CloudWebDriver

        return CloudWebDriver(settings=settings, templates_root=templates_root)
    raise ValueError(f"unknown game driver: {driver_name}")
