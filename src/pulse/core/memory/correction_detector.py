from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CorrectionDetector:
    """Detect user corrections and record them for preference learning.

    Architecture spec §6.3 / §6.8: Detects when a user corrects the AI's
    previous output, extracts the correction signal, and writes to the
    corrections table + triggers DPO pair collection.
    """

    CORRECTION_SIGNALS_ZH = [
        "不对", "错了", "不是这样", "别这样", "我说的是", "我的意思是",
        "重新", "再来", "改一下", "不要", "以后别", "搞错了", "纠正",
        "我纠正", "更正", "不准确", "理解错了",
    ]
    CORRECTION_SIGNALS_EN = [
        "no,", "wrong", "that's not", "i meant", "i said", "actually,",
        "correct that", "not what i", "let me clarify", "fix that",
        "redo", "try again",
    ]

    def __init__(
        self,
        *,
        llm_router: Any | None = None,
        dpo_collector: Any | None = None,
        recall_memory: Any | None = None,
        core_memory: Any | None = None,
        governance: Any | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._dpo_collector = dpo_collector
        self._recall_memory = recall_memory
        self._core_memory = core_memory
        self._governance = governance

    def check(
        self,
        *,
        user_text: str,
        previous_assistant_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Check if user_text is a correction of previous_assistant_text.

        Returns correction info dict if detected, None otherwise.
        """
        safe_user = str(user_text or "").strip()
        safe_prev = str(previous_assistant_text or "").strip()
        if not safe_user or not safe_prev:
            return None

        is_correction = False
        method = "none"

        if self._llm_router is not None:
            try:
                result = self._detect_with_llm(safe_user, safe_prev)
                if result is not None:
                    is_correction = bool(result.get("is_correction"))
                    method = "llm"
            except Exception as exc:
                logger.warning("correction llm detection failed: %s", exc)

        if not is_correction:
            is_correction = self._detect_with_heuristics(safe_user)
            if is_correction:
                method = "heuristic"

        if not is_correction:
            return None

        correction = {
            "is_correction": True,
            "user_text": safe_user,
            "previous_assistant_text": safe_prev[:500],
            "detection_method": method,
        }

        if self._dpo_collector is not None:
            try:
                self._dpo_collector.add_pair(
                    prompt=safe_user,
                    chosen=safe_user,
                    rejected=safe_prev[:1000],
                    metadata=dict(metadata or {}),
                )
            except Exception as exc:
                logger.warning("correction dpo collection failed: %s", exc)

        if self._recall_memory is not None:
            try:
                session_id = str((metadata or {}).get("session_id") or "default")
                self._recall_memory.record_tool_call(
                    session_id=session_id,
                    tool_name="_correction_detected",
                    tool_args={"user_text": safe_user[:200]},
                    tool_result=correction,
                    status="correction",
                )
            except Exception as exc:
                logger.warning("correction tool-call audit failed: %s", exc)

        self._extract_and_update_prefs(safe_user, safe_prev)

        return correction

    def _extract_and_update_prefs(self, user_text: str, prev_text: str) -> None:
        """Track A §6.6: Extract a preference rule from the correction and write to PREFS."""
        if self._core_memory is None:
            return
        rule: str | None = None
        if self._llm_router is not None:
            try:
                prompt = (
                    "The user corrected the assistant. Extract a concise preference rule "
                    "that should be remembered for future interactions.\n"
                    "Return JSON: {\"rule_key\": \"short_snake_case_key\", \"rule_value\": \"description\"}\n"
                    "Return ONLY valid JSON. If no clear rule, return {\"rule_key\": \"\", \"rule_value\": \"\"}.\n\n"
                    f"User correction: {user_text[:300]}\n"
                    f"Previous assistant response: {prev_text[:300]}"
                )
                raw = self._llm_router.invoke_text(prompt, route="classification")
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    lines = cleaned.split("\n")
                    cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                parsed = json.loads(cleaned)
                rk = str(parsed.get("rule_key") or "").strip()
                rv = str(parsed.get("rule_value") or "").strip()
                if rk and rv:
                    rule = f"{rk}: {rv}"
            except Exception as exc:
                logger.warning("correction preference extraction failed: %s", exc)
        if rule is None:
            for sig in ["以后别", "不要", "以后不要", "别再"]:
                if sig in user_text:
                    rule = user_text[:200]
                    break
        if rule:
            try:
                payload = {f"correction_{hash(rule) & 0xFFFF:04x}": rule}
                if self._governance is not None:
                    self._governance.apply_preference_updates(
                        updates=payload,
                        source="correction_detector",
                        actor="correction_detector",
                        risk_level="low",
                    )
                elif self._core_memory is not None:
                    self._core_memory.update_preferences(payload)
            except Exception as exc:
                logger.warning("correction preference update failed: %s", exc)

    def _detect_with_llm(self, user_text: str, prev_text: str) -> dict[str, Any] | None:
        instruction = (
            "Determine if the user's message is correcting or disagreeing with the assistant's previous response.\n"
            "Return JSON: {\"is_correction\": true/false, \"reason\": \"...\"}\n"
            "Return ONLY valid JSON.\n\n"
            f"Assistant's previous response: {prev_text[:300]}\n"
            f"User's new message: {user_text[:300]}"
        )
        raw = self._llm_router.invoke_text(instruction, route="classification")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(cleaned)
        return dict(parsed) if isinstance(parsed, dict) else None

    def _detect_with_heuristics(self, user_text: str) -> bool:
        lowered = user_text.lower().strip()
        for signal in self.CORRECTION_SIGNALS_ZH:
            if signal in lowered:
                return True
        for signal in self.CORRECTION_SIGNALS_EN:
            if lowered.startswith(signal) or f" {signal}" in f" {lowered}":
                return True
        return False
