from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class BehaviorAnalyzer:
    """Analyze implicit user behavior signals for preference learning.

    Architecture spec §6.6 Track A: Detects patterns like ignored notifications,
    repeated tool usage, time-of-day preferences, etc. and proposes PREFS updates.
    """

    def __init__(
        self,
        *,
        llm_router: Any | None = None,
        recall_memory: Any | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._recall_memory = recall_memory

    def analyze_recent_behavior(
        self,
        *,
        session_id: str = "default",
        lookback_turns: int = 50,
    ) -> list[dict[str, Any]]:
        """Analyze recent interaction history and propose preference updates.

        Returns a list of proposed preference updates, each with:
        - key: preference key
        - value: proposed value
        - evidence: why this is proposed
        - confidence: 0-1
        """
        if self._recall_memory is None:
            return []

        recent = self._recall_memory.recent(limit=lookback_turns, session_id=session_id)
        if not recent or len(recent) < 5:
            return []

        if self._llm_router is not None:
            try:
                return self._analyze_with_llm(recent)
            except Exception:
                pass

        return self._analyze_with_heuristics(recent)

    def _analyze_with_llm(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summary_lines: list[str] = []
        for entry in history[-30:]:
            role = str(entry.get("role") or "")
            text = str(entry.get("text") or "")[:150]
            summary_lines.append(f"[{role}] {text}")
        conversation = "\n".join(summary_lines)

        instruction = (
            "Analyze this conversation history and identify any implicit user preferences or patterns.\n"
            "Look for:\n"
            "- Topics the user consistently asks about or avoids\n"
            "- Preferred response style (brief vs detailed)\n"
            "- Time patterns or routine behaviors\n"
            "- Tools or features used frequently\n"
            "- Things the user seems to dislike or ignore\n\n"
            "Return a JSON array of proposed preferences:\n"
            '[{"key": "...", "value": "...", "evidence": "...", "confidence": 0.0-1.0}]\n'
            "Return ONLY valid JSON array. Empty array if no patterns found.\n\n"
            f"Conversation:\n{conversation[:3000]}"
        )
        raw = self._llm_router.invoke_text(instruction, route="classification")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            return []
        return [
            {
                "key": str(item.get("key") or ""),
                "value": item.get("value"),
                "evidence": str(item.get("evidence") or ""),
                "confidence": max(0.0, min(1.0, float(item.get("confidence") or 0.5))),
            }
            for item in parsed
            if isinstance(item, dict) and item.get("key")
        ]

    @staticmethod
    def _analyze_with_heuristics(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []

        user_messages = [e for e in history if str(e.get("role") or "") == "user"]
        if not user_messages:
            return proposals

        short_count = sum(1 for m in user_messages if len(str(m.get("text") or "")) < 20)
        if len(user_messages) > 10 and short_count / len(user_messages) > 0.7:
            proposals.append({
                "key": "response_style",
                "value": "concise",
                "evidence": f"{short_count}/{len(user_messages)} messages are very short, suggesting preference for brevity",
                "confidence": 0.6,
            })

        return proposals
