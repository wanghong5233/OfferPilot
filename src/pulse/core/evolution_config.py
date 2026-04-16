from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_MODES = {"autonomous", "supervised", "gated"}
VALID_RISKS = {"low", "medium", "high", "critical"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_config_path(config_path: str | None, *, default_rel_path: str) -> Path:
    raw = (config_path or "").strip()
    if not raw:
        return (_repo_root() / default_rel_path).resolve()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (_repo_root() / candidate).resolve()


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_mode(value: Any, *, fallback: str) -> str:
    safe = str(value or "").strip().lower()
    if safe in VALID_MODES:
        return safe
    return str(fallback or "autonomous").strip().lower() or "autonomous"


def _normalize_risk(value: Any) -> str:
    safe = str(value or "").strip().lower()
    if safe in VALID_RISKS:
        return safe
    return ""


def build_evolution_governance_options(
    *,
    config_path: str | None = None,
    default_mode_override: str | None = None,
    change_mode_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved_path = _resolve_config_path(config_path, default_rel_path="config/evolution_rules.json")
    rules = _safe_read_json(resolved_path)

    default_mode = _normalize_mode(
        default_mode_override if str(default_mode_override or "").strip() else rules.get("default_mode"),
        fallback="autonomous",
    )

    change_modes: dict[str, str] = {}
    file_change_modes = rules.get("change_modes")
    if isinstance(file_change_modes, dict):
        for key, value in file_change_modes.items():
            safe_key = str(key or "").strip()
            if not safe_key:
                continue
            change_modes[safe_key] = _normalize_mode(value, fallback=default_mode)
    for key, value in dict(change_mode_overrides or {}).items():
        safe_key = str(key or "").strip()
        if not safe_key:
            continue
        if str(value or "").strip():
            change_modes[safe_key] = _normalize_mode(value, fallback=default_mode)

    risk_mode_overrides: dict[str, str] = {}
    file_risk_modes = rules.get("risk_mode_overrides")
    if isinstance(file_risk_modes, dict):
        for risk_key, mode_value in file_risk_modes.items():
            safe_risk = _normalize_risk(risk_key)
            if not safe_risk:
                continue
            risk_mode_overrides[safe_risk] = _normalize_mode(mode_value, fallback=default_mode)

    change_risk_mode_overrides: dict[str, dict[str, str]] = {}
    file_change_risk_modes = rules.get("change_risk_mode_overrides")
    if isinstance(file_change_risk_modes, dict):
        for change_type, mapping in file_change_risk_modes.items():
            safe_type = str(change_type or "").strip()
            if not safe_type or not isinstance(mapping, dict):
                continue
            row: dict[str, str] = {}
            for risk_key, mode_value in mapping.items():
                safe_risk = _normalize_risk(risk_key)
                if not safe_risk:
                    continue
                row[safe_risk] = _normalize_mode(mode_value, fallback=default_mode)
            if row:
                change_risk_mode_overrides[safe_type] = row

    return {
        "resolved_path": str(resolved_path),
        "default_mode": default_mode,
        "change_modes": change_modes,
        "risk_mode_overrides": risk_mode_overrides,
        "change_risk_mode_overrides": change_risk_mode_overrides,
    }
