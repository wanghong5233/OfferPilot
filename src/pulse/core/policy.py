from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

PolicyAction = Literal["safe", "confirm", "blocked"]
PolicyPredicate = Callable[[str, str, dict[str, Any]], bool]


@dataclass(slots=True)
class PolicyDecision:
    action: PolicyAction
    reason: str
    matched_rule: str | None = None


@dataclass(slots=True)
class PolicyRule:
    name: str
    action: PolicyAction
    predicate: PolicyPredicate
    reason: str


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


class PolicyEngine:
    """Policy evaluator for safe/confirm/blocked action gates."""

    def __init__(
        self,
        *,
        blocked_keywords: tuple[str, ...] | None = None,
        confirm_keywords: tuple[str, ...] | None = None,
    ) -> None:
        self._blocked_keywords = tuple(
            _normalize(item) for item in (blocked_keywords or ("rm -rf", "drop database", "delete all"))
        )
        self._confirm_keywords = tuple(
            _normalize(item) for item in (confirm_keywords or ("deploy", "production", "payment", "purchase"))
        )
        self._rules: list[PolicyRule] = []
        self._intent_policies: dict[str, tuple[PolicyAction, str]] = {}

    def register_rule(
        self,
        *,
        name: str,
        action: PolicyAction,
        predicate: PolicyPredicate,
        reason: str,
    ) -> None:
        safe_name = str(name or "").strip()
        if not safe_name:
            raise ValueError("rule name must be non-empty")
        self._rules.append(
            PolicyRule(
                name=safe_name,
                action=action,
                predicate=predicate,
                reason=str(reason or "").strip() or safe_name,
            )
        )

    def set_intent_policy(self, intent: str, *, action: PolicyAction, reason: str) -> None:
        safe_intent = _normalize(intent)
        if not safe_intent:
            raise ValueError("intent must be non-empty")
        self._intent_policies[safe_intent] = (action, str(reason or "").strip() or f"intent={safe_intent}")

    def evaluate(
        self,
        *,
        intent: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        safe_intent = _normalize(intent)
        safe_text = _normalize(text)
        safe_metadata = metadata or {}

        blocked_hit = self._match_keyword(safe_text, self._blocked_keywords)
        if blocked_hit:
            return PolicyDecision(
                action="blocked",
                reason=f"blocked keyword matched: {blocked_hit}",
                matched_rule="blocked_keywords",
            )

        for rule in self._rules:
            try:
                matched = bool(rule.predicate(safe_intent, safe_text, safe_metadata))
            except Exception:
                matched = False
            if matched:
                return PolicyDecision(action=rule.action, reason=rule.reason, matched_rule=rule.name)

        confirm_hit = self._match_keyword(safe_text, self._confirm_keywords)
        if confirm_hit:
            return PolicyDecision(
                action="confirm",
                reason=f"confirm keyword matched: {confirm_hit}",
                matched_rule="confirm_keywords",
            )

        intent_policy = self._intent_policies.get(safe_intent)
        if intent_policy is not None:
            action, reason = intent_policy
            return PolicyDecision(action=action, reason=reason, matched_rule=f"intent:{safe_intent}")

        return PolicyDecision(action="safe", reason="no policy rule matched", matched_rule=None)

    @staticmethod
    def _match_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
        for keyword in keywords:
            if keyword and keyword in text:
                return keyword
        return None
