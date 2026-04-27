"""Game automation driver contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GameDriver(ABC):
    """ABC for emulator / cloud-game automation drivers.

    Public methods return dictionaries to align with existing platform
    connector contracts under ``modules/job/_connectors``.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def execution_ready(self) -> bool:
        ...

    @abstractmethod
    def health(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def app_in_foreground(self, *, package_name: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def installed_packages(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def screenshot(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def tap(self, *, x: int, y: int) -> dict[str, Any]:
        ...

    @abstractmethod
    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> dict[str, Any]:
        ...

    @abstractmethod
    def text(self, *, value: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def find_template(
        self,
        *,
        image_bytes: bytes,
        template_path: str,
        threshold: float = 0.9,
    ) -> dict[str, Any]:
        ...

    def find_text(self, *, image_bytes: bytes, query: str) -> dict[str, Any]:
        _ = image_bytes, query
        return {
            "ok": False,
            "source": self.provider_name,
            "status": "not_implemented",
            "error": "ocr_not_available",
            "error_message": "OCR is not implemented by this driver.",
        }
