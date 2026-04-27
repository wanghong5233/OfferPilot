"""Cloud-web driver stub.

This class proves the Game pipeline depends on ``GameDriver`` rather than ADB
directly. It intentionally does not connect Patchright or any cloud-game
provider in this PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import GameSettings
from ..base import GameDriver


class CloudWebDriver(GameDriver):
    provider_name = "cloud_web"

    def __init__(self, *, settings: GameSettings, templates_root: Path) -> None:
        self._settings = settings
        self._templates_root = templates_root

    @property
    def execution_ready(self) -> bool:
        return False

    def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "source": self.provider_name,
            "status": "not_implemented",
            "error": "cloud_web_not_implemented",
            "error_message": "cloud_web driver is a stub; Patchright integration is out of scope.",
        }

    def app_in_foreground(self, *, package_name: str) -> dict[str, Any]:
        _ = package_name
        return self._not_implemented("app_in_foreground")

    def installed_packages(self) -> dict[str, Any]:
        return self._not_implemented("installed_packages")

    def screenshot(self) -> dict[str, Any]:
        return self._not_implemented("screenshot")

    def tap(self, *, x: int, y: int) -> dict[str, Any]:
        _ = x, y
        return self._not_implemented("tap")

    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> dict[str, Any]:
        _ = x1, y1, x2, y2, duration_ms
        return self._not_implemented("swipe")

    def text(self, *, value: str) -> dict[str, Any]:
        _ = value
        return self._not_implemented("text")

    def find_template(
        self,
        *,
        image_bytes: bytes,
        template_path: str,
        threshold: float = 0.9,
    ) -> dict[str, Any]:
        _ = image_bytes, template_path, threshold
        return self._not_implemented("find_template")

    def _not_implemented(self, method: str) -> dict[str, Any]:
        return {
            "ok": False,
            "source": self.provider_name,
            "status": "not_implemented",
            "error": f"{method}_not_implemented",
            "error_message": f"cloud_web.{method} is not implemented.",
        }
