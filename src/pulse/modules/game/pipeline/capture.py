"""Capture stage for Game workflow."""

from __future__ import annotations

from .._connectors import GameDriver
from .types import Screenshot


def capture_screen(driver: GameDriver) -> Screenshot:
    result = driver.screenshot()
    if not result.get("ok"):
        error = str(result.get("error") or "screenshot_failed")
        message = str(result.get("error_message") or error)
        raise RuntimeError(f"{error}: {message}")
    image_bytes = result.get("image_bytes")
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise RuntimeError("screenshot_failed: driver returned empty image_bytes")
    return Screenshot(image_bytes=image_bytes, ref=str(result.get("screenshot_ref") or ""))
