from __future__ import annotations

from typing import Any

from pulse.core.router import IntentRouter


class _FakeLLMRouter:
    def __init__(self, output: Any) -> None:
        self._output = output

    def invoke_structured(self, prompt_value: Any, schema: type[Any], *, route: str = "default") -> Any:  # noqa: ARG002
        if isinstance(self._output, Exception):
            raise self._output
        if isinstance(self._output, dict):
            return schema(**self._output)
        return self._output


def test_intent_router_exact_match() -> None:
    router = IntentRouter(fallback_intent="general.default")
    router.register_intent("jobs.scan", target="modules.boss_greet")
    router.register_exact("scan jobs", intent="jobs.scan")
    result = router.resolve("scan jobs")
    assert result.method == "exact"
    assert result.intent == "jobs.scan"
    assert result.target == "modules.boss_greet"


def test_intent_router_prefix_prefers_longest() -> None:
    router = IntentRouter(fallback_intent="general.default")
    router.register_intent("jobs.scan", target="modules.boss_greet")
    router.register_intent("jobs.scan.deep", target="modules.boss_greet")
    router.register_prefix("/scan", intent="jobs.scan")
    router.register_prefix("/scan detail", intent="jobs.scan.deep")
    result = router.resolve("/scan detail for ai")
    assert result.method == "prefix"
    assert result.intent == "jobs.scan.deep"


def test_intent_router_llm_fallback_when_no_rule() -> None:
    router = IntentRouter(
        llm_router=_FakeLLMRouter({"intent": "email.process", "confidence": 0.88, "reason": "closest"}),
        fallback_intent="general.default",
    )
    router.register_intent("email.process", target="modules.email_tracker")
    result = router.resolve("check unread emails")
    assert result.method == "llm"
    assert result.intent == "email.process"
    assert result.target == "modules.email_tracker"
    assert result.confidence == 0.88


def test_intent_router_returns_global_fallback() -> None:
    router = IntentRouter(
        llm_router=_FakeLLMRouter(RuntimeError("llm offline")),
        fallback_intent="general.default",
        fallback_target="modules.hello",
    )
    result = router.resolve("unknown command")
    assert result.method == "fallback"
    assert result.intent == "general.default"
    assert result.target == "modules.hello"
