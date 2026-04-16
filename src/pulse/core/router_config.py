from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .router import IntentRouter


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


def build_intent_router(
    *,
    llm_router: Any | None = None,
    config_path: str | None = None,
    fallback_intent: str = "general.default",
    fallback_target: str | None = None,
) -> IntentRouter:
    resolved_path = _resolve_config_path(config_path, default_rel_path="config/router_rules.json")
    rules = _safe_read_json(resolved_path)

    configured_fallback_intent = str(rules.get("fallback_intent") or fallback_intent).strip() or fallback_intent
    configured_fallback_target = str(rules.get("fallback_target") or fallback_target or "").strip() or fallback_target

    router = IntentRouter(
        llm_router=llm_router,
        fallback_intent=configured_fallback_intent,
        fallback_target=configured_fallback_target,
    )

    intents = rules.get("intents")
    if isinstance(intents, dict):
        for intent, target in intents.items():
            safe_intent = str(intent or "").strip()
            safe_target = str(target or "").strip()
            if safe_intent and safe_target:
                router.register_intent(safe_intent, target=safe_target)

    exact_rules = rules.get("exact")
    if isinstance(exact_rules, list):
        for item in exact_rules:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            intent = str(item.get("intent") or "").strip()
            if key and intent:
                router.register_exact(key, intent=intent)

    prefix_rules = rules.get("prefix")
    if isinstance(prefix_rules, list):
        for item in prefix_rules:
            if not isinstance(item, dict):
                continue
            prefix = str(item.get("prefix") or "").strip()
            intent = str(item.get("intent") or "").strip()
            if prefix and intent:
                router.register_prefix(prefix, intent=intent)

    if not router.known_intents():
        router.register_intent(configured_fallback_intent, target=configured_fallback_target or "hello")
        router.register_exact("ping", intent=configured_fallback_intent)
    return router
