from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .policy import PolicyAction, PolicyEngine


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


def _csv_keywords(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return tuple()
    values = [item.strip() for item in str(raw).split(",")]
    return tuple(item for item in values if item)


def _build_rule_predicate(item: dict[str, Any]):
    intents = {str(v).strip().lower() for v in item.get("intents_any", []) if str(v).strip()}  # type: ignore[arg-type]
    text_keywords = [str(v).strip().lower() for v in item.get("text_contains_any", []) if str(v).strip()]  # type: ignore[arg-type]
    metadata_keys = [str(v).strip() for v in item.get("metadata_keys", []) if str(v).strip()]  # type: ignore[arg-type]

    def _predicate(intent: str, text: str, metadata: dict[str, Any]) -> bool:
        if intents and intent not in intents:
            return False
        if text_keywords and not any(keyword in text for keyword in text_keywords):
            return False
        if metadata_keys and not all(key in metadata for key in metadata_keys):
            return False
        return True

    return _predicate


def build_policy_engine(
    *,
    config_path: str | None = None,
    blocked_keywords_env: str | None = None,
    confirm_keywords_env: str | None = None,
) -> PolicyEngine:
    resolved_path = _resolve_config_path(config_path, default_rel_path="config/policy_rules.json")
    rules = _safe_read_json(resolved_path)

    blocked_from_file = tuple(str(item).strip() for item in rules.get("blocked_keywords", []) if str(item).strip())
    confirm_from_file = tuple(str(item).strip() for item in rules.get("confirm_keywords", []) if str(item).strip())
    blocked_from_env = _csv_keywords(blocked_keywords_env)
    confirm_from_env = _csv_keywords(confirm_keywords_env)

    engine = PolicyEngine(
        blocked_keywords=blocked_from_env or blocked_from_file or None,
        confirm_keywords=confirm_from_env or confirm_from_file or None,
    )

    intent_policies = rules.get("intent_policies")
    if isinstance(intent_policies, dict):
        for intent, item in intent_policies.items():
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip().lower()
            reason = str(item.get("reason") or "").strip()
            if action in {"safe", "confirm", "blocked"}:
                engine.set_intent_policy(
                    str(intent),
                    action=action,  # type: ignore[arg-type]
                    reason=reason or f"intent policy: {intent}",
                )

    custom_rules = rules.get("rules")
    if isinstance(custom_rules, list):
        for item in custom_rules:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            action = str(item.get("action") or "").strip().lower()
            reason = str(item.get("reason") or "").strip()
            if not name or action not in {"safe", "confirm", "blocked"}:
                continue
            engine.register_rule(
                name=name,
                action=action,  # type: ignore[arg-type]
                predicate=_build_rule_predicate(item),
                reason=reason or name,
            )
    return engine
