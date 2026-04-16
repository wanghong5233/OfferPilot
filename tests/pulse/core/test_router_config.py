from __future__ import annotations

import json

from pulse.core.router_config import build_intent_router


def test_build_router_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "router_rules.json"
    config_path.write_text(
        json.dumps(
            {
                "fallback_intent": "general.default",
                "fallback_target": "hello",
                "intents": {
                    "general.default": "hello",
                    "email.fetch": "email_tracker",
                },
                "exact": [{"key": "fetch email", "intent": "email.fetch"}],
                "prefix": [{"prefix": "/email", "intent": "email.fetch"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    router = build_intent_router(config_path=str(config_path))
    exact = router.resolve("fetch email")
    assert exact.method == "exact"
    assert exact.intent == "email.fetch"
    assert exact.target == "email_tracker"

    pref = router.resolve("/email unread")
    assert pref.method == "prefix"
    assert pref.intent == "email.fetch"


def test_build_router_falls_back_when_config_missing(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    router = build_intent_router(config_path=str(missing), fallback_intent="general.default", fallback_target="hello")
    result = router.resolve("ping")
    assert result.intent == "general.default"
    assert result.target == "hello"
