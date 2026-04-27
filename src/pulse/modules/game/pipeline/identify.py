"""Identify stage for Game workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._connectors import GameDriver
from ..games import GameConfig, GameTaskConfig
from .types import Screenshot


def identify_task_action(
    *,
    driver: GameDriver,
    game: GameConfig,
    task: GameTaskConfig,
    screenshot: Screenshot,
    templates_root: Path,
) -> dict[str, Any]:
    if task.type == "tap_template":
        return _find_template(
            driver=driver,
            game=game,
            template=task.template,
            screenshot=screenshot,
            templates_root=templates_root,
        )
    if task.type == "claim_chain":
        return {"ok": True, "source": "workflow", "action_type": "claim_chain"}
    if task.type == "gacha":
        template = str(task.params.get("template") or task.template or "").strip()
        if not template:
            return {
                "ok": False,
                "source": "workflow",
                "status": "unknown_screen",
                "error": "gacha_template_missing",
                "error_message": "gacha task requires params.template before execution.",
            }
        return _find_template(
            driver=driver,
            game=game,
            template=template,
            screenshot=screenshot,
            templates_root=templates_root,
        )
    if task.type in {"swipe", "wait"}:
        return {"ok": True, "source": "workflow", "action_type": task.type}
    return {
        "ok": False,
        "source": "workflow",
        "status": "unknown_screen",
        "error": "unsupported_task_type",
        "error_message": f"Unsupported task type: {task.type}",
    }


def _find_template(
    *,
    driver: GameDriver,
    game: GameConfig,
    template: str,
    screenshot: Screenshot,
    templates_root: Path,
    threshold: float = 0.9,
) -> dict[str, Any]:
    result = driver.find_template(
        image_bytes=screenshot.image_bytes,
        template_path=str(templates_root / game.templates_dir / template),
        threshold=threshold,
    )
    if result.get("ok") and result.get("found"):
        return {
            "ok": True,
            "source": result.get("source") or driver.provider_name,
            "action_type": "tap",
            "template": template,
            "x": int(result.get("x") or 0),
            "y": int(result.get("y") or 0),
            "score": float(result.get("score") or 0.0),
        }
    return {
        "ok": False,
        "source": result.get("source") or driver.provider_name,
        "status": "unknown_screen",
        "error": result.get("error") or "template_not_found",
        "error_message": result.get("error_message") or f"Template not found: {template}",
        "template": template,
    }
