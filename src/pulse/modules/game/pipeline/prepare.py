"""Prepare stage for Game workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._connectors import GameDriver
from ..games import GameConfig


def prepare_game(
    driver: GameDriver,
    game: GameConfig,
    *,
    templates_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    health = driver.health()
    if not health.get("ok"):
        return {
            "ok": False,
            "status": "not_ready",
            "error": health.get("error") or "driver_not_ready",
            "error_message": health.get("error_message") or "Game driver is not ready.",
            "health": health,
        }

    packages_result = driver.installed_packages()
    if not packages_result.get("ok"):
        return {
            "ok": False,
            "status": "not_ready",
            "error": packages_result.get("error") or "package_probe_failed",
            "error_message": packages_result.get("error_message") or "Unable to list installed packages.",
        }
    packages = set(packages_result.get("packages") or [])
    package_name = next((pkg for pkg in game.package_candidates if pkg in packages), "")
    if not package_name:
        return {
            "ok": False,
            "status": "not_ready",
            "error": "game_package_missing",
            "error_message": f"None of package_candidates are installed for {game.id}.",
            "package_candidates": list(game.package_candidates),
        }

    foreground = driver.app_in_foreground(package_name=package_name)
    if foreground.get("ok") and not foreground.get("foreground"):
        return {
            "ok": False,
            "status": "not_ready",
            "error": "game_not_foreground",
            "error_message": f"{package_name} is installed but not in foreground.",
            "package_name": package_name,
        }
    if not foreground.get("ok"):
        return {
            "ok": False,
            "status": "not_ready",
            "error": foreground.get("error") or "foreground_probe_failed",
            "error_message": foreground.get("error_message") or "Unable to inspect foreground app.",
            "package_name": package_name,
        }

    risk = _detect_risk_control(driver, game, templates_root=templates_root)
    if risk.get("detected"):
        return {
            "ok": False,
            "status": "aborted_risk_control",
            "error": "risk_control_detected",
            "error_message": f"Risk-control screen detected: {risk.get('template')}",
            "package_name": package_name,
            "risk": risk,
        }
    if not dry_run and not risk.get("verified", True):
        return {
            "ok": False,
            "status": "not_ready",
            "error": "risk_control_unverified",
            "error_message": "Risk-control templates are configured but cannot be verified.",
            "package_name": package_name,
            "risk": risk,
        }

    return {
        "ok": True,
        "status": "ready",
        "package_name": package_name,
        "health": health,
        "risk": risk,
    }


def _detect_risk_control(driver: GameDriver, game: GameConfig, *, templates_root: Path) -> dict[str, Any]:
    templates = list(game.risk_control.templates or [])
    if not templates:
        return {"detected": False, "verified": True, "checked": 0}
    screenshot = driver.screenshot()
    if not screenshot.get("ok"):
        return {
            "detected": False,
            "verified": False,
            "checked": 0,
            "status": "screenshot_failed",
            "error": screenshot.get("error"),
            "error_message": screenshot.get("error_message"),
        }
    image_bytes = screenshot.get("image_bytes")
    if not isinstance(image_bytes, bytes):
        return {
            "detected": False,
            "verified": False,
            "checked": 0,
            "status": "invalid_screenshot",
        }
    for template in templates:
        result = driver.find_template(
            image_bytes=image_bytes,
            template_path=str(templates_root / game.templates_dir / template),
            threshold=0.88,
        )
        if result.get("ok") and result.get("found"):
            return {"detected": True, "verified": True, "template": template, "match": result}
        if not result.get("ok"):
            return {
                "detected": False,
                "verified": False,
                "checked": 0,
                "status": "template_check_failed",
                "template": template,
                "error": result.get("error") or "template_check_failed",
                "error_message": result.get("error_message") or "Unable to verify risk-control template.",
            }
    return {"detected": False, "verified": True, "checked": len(templates)}
