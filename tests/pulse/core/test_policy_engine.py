from __future__ import annotations

from pulse.core.policy import PolicyEngine


def test_policy_engine_blocks_high_risk_keyword() -> None:
    engine = PolicyEngine(blocked_keywords=("rm -rf",), confirm_keywords=("deploy",))
    result = engine.evaluate(intent="ops.shell", text="please run rm -rf /tmp", metadata={})
    assert result.action == "blocked"
    assert result.matched_rule == "blocked_keywords"


def test_policy_engine_requires_confirm_on_sensitive_keyword() -> None:
    engine = PolicyEngine(blocked_keywords=("drop database",), confirm_keywords=("deploy",))
    result = engine.evaluate(intent="ops.deploy", text="deploy to production now", metadata={})
    assert result.action == "confirm"
    assert result.matched_rule == "confirm_keywords"


def test_policy_engine_supports_intent_policy() -> None:
    engine = PolicyEngine(blocked_keywords=(), confirm_keywords=())
    engine.set_intent_policy("finance.transfer", action="confirm", reason="money transfer requires approval")
    result = engine.evaluate(intent="finance.transfer", text="transfer 100", metadata={})
    assert result.action == "confirm"
    assert result.matched_rule == "intent:finance.transfer"


def test_policy_engine_custom_rule_can_block() -> None:
    engine = PolicyEngine(blocked_keywords=(), confirm_keywords=())
    engine.register_rule(
        name="night_window",
        action="blocked",
        reason="night execution blocked",
        predicate=lambda intent, text, metadata: bool(metadata.get("night_mode")),  # noqa: ARG005
    )
    result = engine.evaluate(intent="ops.run", text="run task", metadata={"night_mode": True})
    assert result.action == "blocked"
    assert result.matched_rule == "night_window"
