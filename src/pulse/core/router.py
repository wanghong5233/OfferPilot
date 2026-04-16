from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field


class _LLMIntentOutput(BaseModel):
    intent: str = Field(..., min_length=1, max_length=120)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=300)


class StructuredInvoker(Protocol):
    def invoke_structured(self, prompt_value: Any, schema: type[Any], *, route: str = "default") -> Any:
        ...


@dataclass(slots=True)
class RouteDecision:
    intent: str
    target: str | None
    method: str  # exact | prefix | llm | fallback
    confidence: float
    reason: str


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


class IntentRouter:
    """Intent resolver with exact, prefix, and LLM fallback stages."""

    def __init__(
        self,
        *,
        llm_router: StructuredInvoker | None = None,
        fallback_intent: str = "general.default",
        fallback_target: str | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._fallback_intent = fallback_intent
        self._fallback_target = fallback_target
        self._intent_targets: dict[str, str] = {}
        self._exact_routes: dict[str, str] = {}
        self._prefix_routes: list[tuple[str, str]] = []

    def register_intent(self, intent: str, *, target: str) -> None:
        safe_intent = _normalize(intent)
        if not safe_intent:
            raise ValueError("intent must be non-empty")
        self._intent_targets[safe_intent] = str(target).strip() or target

    def register_exact(self, key: str, *, intent: str) -> None:
        safe_key = _normalize(key)
        safe_intent = _normalize(intent)
        if not safe_key or not safe_intent:
            raise ValueError("exact route key/intent must be non-empty")
        self._exact_routes[safe_key] = safe_intent

    def register_prefix(self, prefix: str, *, intent: str) -> None:
        safe_prefix = _normalize(prefix)
        safe_intent = _normalize(intent)
        if not safe_prefix or not safe_intent:
            raise ValueError("prefix route key/intent must be non-empty")
        self._prefix_routes.append((safe_prefix, safe_intent))
        self._prefix_routes.sort(key=lambda item: len(item[0]), reverse=True)

    def known_intents(self) -> list[str]:
        return sorted(self._intent_targets.keys())

    def resolve(self, text: str) -> RouteDecision:
        normalized = _normalize(text)
        if normalized in self._exact_routes:
            intent = self._exact_routes[normalized]
            return RouteDecision(
                intent=intent,
                target=self._intent_targets.get(intent),
                method="exact",
                confidence=1.0,
                reason=f"exact matched: {normalized}",
            )

        for prefix, intent in self._prefix_routes:
            if normalized.startswith(prefix):
                return RouteDecision(
                    intent=intent,
                    target=self._intent_targets.get(intent),
                    method="prefix",
                    confidence=0.95,
                    reason=f"prefix matched: {prefix}",
                )

        llm_result = self._resolve_with_llm(text)
        if llm_result is not None:
            return llm_result

        fallback_intent = _normalize(self._fallback_intent)
        return RouteDecision(
            intent=fallback_intent,
            target=self._intent_targets.get(fallback_intent, self._fallback_target),
            method="fallback",
            confidence=0.2,
            reason="no exact/prefix/llm match",
        )

    def _resolve_with_llm(self, text: str) -> RouteDecision | None:
        if self._llm_router is None:
            return None
        intents = self.known_intents()
        if not intents:
            return None
        prompt = (
            "Choose the most suitable intent from candidates. "
            "Return JSON with: intent, confidence(0-1), reason.\n"
            f"candidates={intents}\n"
            f"text={text}"
        )
        try:
            output = self._llm_router.invoke_structured(prompt, _LLMIntentOutput, route="classification")
        except Exception:
            return None

        candidate_intent = _normalize(getattr(output, "intent", ""))
        if not candidate_intent or candidate_intent not in self._intent_targets:
            return None
        confidence_raw = getattr(output, "confidence", 0.0)
        try:
            confidence = max(0.0, min(float(confidence_raw), 1.0))
        except Exception:
            confidence = 0.5
        reason = str(getattr(output, "reason", "") or "llm selected")
        return RouteDecision(
            intent=candidate_intent,
            target=self._intent_targets.get(candidate_intent),
            method="llm",
            confidence=confidence,
            reason=reason[:300],
        )
