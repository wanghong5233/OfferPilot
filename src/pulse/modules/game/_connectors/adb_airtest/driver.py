"""ADB-first driver with optional Airtest template matching."""

from __future__ import annotations

import importlib.util
from io import BytesIO
import logging
import subprocess
from pathlib import Path
from typing import Any

from ...config import GameSettings
from ..base import GameDriver

logger = logging.getLogger(__name__)


class AdbAirtestDriver(GameDriver):
    provider_name = "adb_airtest"

    def __init__(self, *, settings: GameSettings, templates_root: Path) -> None:
        self._settings = settings
        self._templates_root = templates_root
        self._resolved_serial: str | None = None

    @property
    def execution_ready(self) -> bool:
        return bool(self.health().get("ok"))

    def health(self) -> dict[str, Any]:
        serial = self._resolve_serial()
        if not serial.get("ok"):
            return serial
        result = self._run_adb(["get-state"], serial=str(serial["serial"]))
        if not result["ok"]:
            return result
        return {
            "ok": True,
            "source": self.provider_name,
            "adb_connected": True,
            "serial": serial["serial"],
            "airtest_available": _module_available("airtest"),
            "template_matching": "airtest" if _module_available("airtest") else "not_implemented",
        }

    def app_in_foreground(self, *, package_name: str) -> dict[str, Any]:
        serial = self._resolve_serial()
        if not serial.get("ok"):
            return serial
        result = self._run_adb(
            ["shell", "dumpsys", "window", "windows"],
            serial=str(serial["serial"]),
        )
        if not result["ok"]:
            return result
        output = str(result.get("stdout") or "")
        foreground = str(package_name) in output
        return {
            "ok": True,
            "source": self.provider_name,
            "foreground": foreground,
            "package_name": package_name,
        }

    def installed_packages(self) -> dict[str, Any]:
        serial = self._resolve_serial()
        if not serial.get("ok"):
            return serial
        result = self._run_adb(["shell", "pm", "list", "packages"], serial=str(serial["serial"]))
        if not result["ok"]:
            return result
        packages = [
            line.removeprefix("package:").strip()
            for line in str(result.get("stdout") or "").splitlines()
            if line.strip().startswith("package:")
        ]
        return {
            "ok": True,
            "source": self.provider_name,
            "packages": packages,
        }

    def screenshot(self) -> dict[str, Any]:
        serial = self._resolve_serial()
        if not serial.get("ok"):
            return serial
        result = self._run_adb(
            ["exec-out", "screencap", "-p"],
            serial=str(serial["serial"]),
            text=False,
        )
        if not result["ok"]:
            return result
        return {
            "ok": True,
            "source": self.provider_name,
            "image_bytes": result["stdout_bytes"],
        }

    def tap(self, *, x: int, y: int) -> dict[str, Any]:
        return self._shell_input(["tap", str(int(x)), str(int(y))])

    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> dict[str, Any]:
        return self._shell_input(
            ["swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms))]
        )

    def text(self, *, value: str) -> dict[str, Any]:
        escaped = str(value).replace(" ", "%s")
        return self._shell_input(["text", escaped])

    def find_template(
        self,
        *,
        image_bytes: bytes,
        template_path: str,
        threshold: float = 0.9,
    ) -> dict[str, Any]:
        if not _module_available("airtest"):
            return {
                "ok": False,
                "source": self.provider_name,
                "status": "not_implemented",
                "error": "airtest_not_available",
                "error_message": "Install airtest after dependency approval to enable template matching.",
            }
        template = self._templates_root / template_path
        if not template.is_file():
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "template_missing",
                "error_message": f"template not found: {template}",
            }
        _ = image_bytes, threshold
        return {
            "ok": False,
            "source": self.provider_name,
            "status": "not_implemented",
            "error": "airtest_adapter_not_wired",
            "error_message": "Airtest import is available but adapter wiring is intentionally deferred.",
        }

    def find_text(self, *, image_bytes: bytes, query: str) -> dict[str, Any]:
        if not _module_available("pytesseract"):
            return {
                "ok": False,
                "source": self.provider_name,
                "status": "not_implemented",
                "error": "tesseract_not_available",
                "error_message": "Install pytesseract and Tesseract-OCR chi_sim to enable OCR.",
            }
        if not _module_available("PIL"):
            return {
                "ok": False,
                "source": self.provider_name,
                "status": "not_implemented",
                "error": "pillow_not_available",
                "error_message": "Pillow is required for pytesseract image loading.",
            }
        try:
            from PIL import Image
            import pytesseract
        except ImportError as exc:
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "ocr_import_failed",
                "error_message": str(exc),
            }
        image = Image.open(BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        needle = str(query or "").strip()
        found = bool(needle and needle in text) if needle else bool(text.strip())
        return {
            "ok": True,
            "source": self.provider_name,
            "found": found,
            "text": text,
        }

    def _shell_input(self, args: list[str]) -> dict[str, Any]:
        serial = self._resolve_serial()
        if not serial.get("ok"):
            return serial
        result = self._run_adb(["shell", "input", *args], serial=str(serial["serial"]))
        if not result["ok"]:
            return result
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def _resolve_serial(self) -> dict[str, Any]:
        if self._resolved_serial:
            return {"ok": True, "source": self.provider_name, "serial": self._resolved_serial}
        configured = self._settings.adb_serial
        if configured:
            self._resolved_serial = configured
            return {"ok": True, "source": self.provider_name, "serial": configured}
        result = self._run_adb(["devices"])
        if not result["ok"]:
            return result
        devices = _parse_adb_devices(str(result.get("stdout") or ""))
        if not devices:
            return {
                "ok": False,
                "source": self.provider_name,
                "status": "not_ready",
                "error": "adb_device_missing",
                "error_message": "No adb device is connected. Start MuMu and run adb connect if needed.",
            }
        if len(devices) > 1:
            return {
                "ok": False,
                "source": self.provider_name,
                "status": "not_ready",
                "error": "adb_multiple_devices",
                "error_message": "Multiple adb devices found; set PULSE_GAME_ADB_SERIAL.",
                "devices": devices,
            }
        self._resolved_serial = devices[0]
        return {"ok": True, "source": self.provider_name, "serial": devices[0]}

    def _run_adb(
        self,
        args: list[str],
        *,
        serial: str | None = None,
        text: bool = True,
    ) -> dict[str, Any]:
        command = ["adb"]
        if serial:
            command.extend(["-s", serial])
        command.extend(args)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=text,
                timeout=self._settings.command_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "adb_timeout",
                "error_message": str(exc),
            }
        except OSError as exc:
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "adb_unavailable",
                "error_message": str(exc),
            }
        if completed.returncode != 0:
            stderr = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode("utf-8", "ignore")
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "adb_command_failed",
                "error_message": stderr.strip() or f"adb exited with {completed.returncode}",
            }
        if text:
            return {"ok": True, "source": self.provider_name, "stdout": completed.stdout}
        return {"ok": True, "source": self.provider_name, "stdout_bytes": completed.stdout}


def _parse_adb_devices(output: str) -> list[str]:
    devices: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
