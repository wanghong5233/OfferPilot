from __future__ import annotations

import json

from pulse.core.policy_config import build_policy_engine


def test_build_policy_engine_from_config(tmp_path) -> None:
    config_path = tmp_path / "policy_rules.json"
    config_path.write_text(
        json.dumps(
            {
                "blocked_keywords": ["dangerous"],
                "confirm_keywords": ["deploy"],
                "intent_policies": {
                    "boss.greet.trigger": {
                        "action": "confirm",
                        "reason": "needs approval",
                    }
                },
                "rules": [
                    {
                        "name": "feishu_sensitive",
                        "action": "confirm",
                        "reason": "group context",
                        "intents_any": ["boss.chat.process"],
                        "metadata_keys": ["chat_id"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = build_policy_engine(config_path=str(config_path))

    blocked = engine.evaluate(intent="general.default", text="very dangerous op", metadata={})
    assert blocked.action == "blocked"

    confirm_by_rule = engine.evaluate(intent="boss.chat.process", text="normal", metadata={"chat_id": "c1"})
    assert confirm_by_rule.action == "confirm"
    assert confirm_by_rule.matched_rule == "feishu_sensitive"

    confirm_by_intent = engine.evaluate(intent="boss.greet.trigger", text="normal", metadata={})
    assert confirm_by_intent.action == "confirm"
    assert confirm_by_intent.matched_rule == "intent:boss.greet.trigger"


def test_policy_env_overrides_keywords(tmp_path) -> None:
    config_path = tmp_path / "policy_rules.json"
    config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    engine = build_policy_engine(
        config_path=str(config_path),
        blocked_keywords_env="wipe all",
        confirm_keywords_env="release",
    )
    blocked = engine.evaluate(intent="general.default", text="please wipe all data", metadata={})
    assert blocked.action == "blocked"
    confirm = engine.evaluate(intent="general.default", text="release today", metadata={})
    assert confirm.action == "confirm"
