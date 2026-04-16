from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any


@dataclass(slots=True)
class CostEvent:
    timestamp: datetime
    route: str
    estimated_tokens: int
    estimated_cost_usd: float
    allowed: bool


class CostController:
    """In-memory daily budget controller with auto-degradation support.

    When budget usage exceeds the degradation threshold, suggests switching
    to cheaper models via the `should_degrade` flag.
    """

    DEGRADE_THRESHOLD_RATIO = 0.8

    def __init__(
        self,
        *,
        daily_budget_usd: float = 2.0,
        usd_per_1k_tokens: float = 0.0025,
    ) -> None:
        self._daily_budget_usd = max(0.0, float(daily_budget_usd))
        self._usd_per_1k_tokens = max(0.0, float(usd_per_1k_tokens))
        self._spent_usd = 0.0
        self._events: list[CostEvent] = []
        self._last_reset_day = datetime.now(timezone.utc).date()
        self._lock = Lock()

    def _reset_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today == self._last_reset_day:
            return
        self._last_reset_day = today
        self._spent_usd = 0.0
        self._events.clear()

    @staticmethod
    def estimate_tokens(*texts: str) -> int:
        chars = sum(len(str(item or "")) for item in texts)
        return max(1, chars // 4)

    def estimate_cost_usd(self, *, tokens: int) -> float:
        return round(max(0, int(tokens)) / 1000.0 * self._usd_per_1k_tokens, 6)

    @property
    def should_degrade(self) -> bool:
        """True when budget usage exceeds threshold — caller should switch to cheaper model."""
        with self._lock:
            self._reset_if_needed()
            return self._should_degrade_unlocked()

    def _should_degrade_unlocked(self) -> bool:
        if self._daily_budget_usd <= 0:
            return True
        return (self._spent_usd / self._daily_budget_usd) >= self.DEGRADE_THRESHOLD_RATIO

    def recommend_route(self, preferred: str = "default") -> str:
        """Return the recommended LLM route based on remaining budget."""
        if self.should_degrade:
            return "cheap"
        return preferred

    def reserve(self, *, route: str, tokens: int) -> bool:
        with self._lock:
            self._reset_if_needed()
            estimate_cost = self.estimate_cost_usd(tokens=tokens)
            allowed = (self._spent_usd + estimate_cost) <= self._daily_budget_usd
            if allowed:
                self._spent_usd = round(self._spent_usd + estimate_cost, 6)
            self._events.append(
                CostEvent(
                    timestamp=datetime.now(timezone.utc),
                    route=str(route or "default"),
                    estimated_tokens=max(0, int(tokens)),
                    estimated_cost_usd=estimate_cost,
                    allowed=allowed,
                )
            )
            if len(self._events) > 300:
                self._events = self._events[-300:]
            return allowed

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._reset_if_needed()
            return {
                "daily_budget_usd": self._daily_budget_usd,
                "spent_usd": round(self._spent_usd, 6),
                "remaining_usd": round(max(0.0, self._daily_budget_usd - self._spent_usd), 6),
                "degraded": self._should_degrade_unlocked(),
                "event_count": len(self._events),
            }
