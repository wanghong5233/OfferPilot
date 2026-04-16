from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


def _clean_value(text: str, *, max_len: int = 80) -> str:
    value = str(text or "").strip().strip("。！？!?，,；;:：")
    value = re.sub(r"\s+", " ", value)
    return value[:max_len].strip()


@dataclass(slots=True)
class PreferenceExtraction:
    prefs_updates: dict[str, Any]
    soul_updates: dict[str, Any]
    evidences: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefs_updates": dict(self.prefs_updates),
            "soul_updates": dict(self.soul_updates),
            "evidences": list(self.evidences),
        }


class PreferenceExtractor:
    """Extract preference/soul update signals from user text.

    Uses LLM when available for nuanced extraction, falls back to
    regex patterns when LLM is not configured.
    """

    _re_default_location = re.compile(
        r"(?:默认城市|默认用|默认使用|以后默认用|以后查天气用)\s*[:：]?\s*([^\s，。！？,!?]{1,20})",
        flags=re.IGNORECASE,
    )
    _re_dislike = re.compile(r"(?:我不喜欢|不要再推荐|以后不要推荐)\s*[:：]?\s*([^。！？!?]{1,80})")
    _re_like = re.compile(r"(?:我喜欢|我偏好|我更喜欢)\s*[:：]?\s*([^。！？!?]{1,80})")
    _re_name = re.compile(r"(?:叫我|以后称呼我)\s*[:：]?\s*([^。！？!?]{1,30})")

    def __init__(self, *, llm_router: Any | None = None) -> None:
        self._llm_router = llm_router

    def extract(self, text: str) -> PreferenceExtraction:
        safe_text = str(text or "").strip()
        if not safe_text:
            return PreferenceExtraction(prefs_updates={}, soul_updates={}, evidences=[])

        if self._llm_router is not None:
            try:
                return self._extract_with_llm(safe_text)
            except Exception:
                pass

        return self._extract_with_regex(safe_text)

    def _extract_with_llm(self, text: str) -> PreferenceExtraction:
        instruction = (
            "Analyze the following user message and extract any explicit preferences or style requests.\n"
            "Return a JSON object with exactly these fields:\n"
            "- prefs_updates: dict of preference key-value pairs (e.g., default_location, like, dislike, preferred_name, language, timezone)\n"
            "- soul_updates: dict of personality/style changes (e.g., tone: 'concise'|'detailed'|'casual'|'formal', style_rules: [...])\n"
            "- evidences: list of strings describing what was detected\n\n"
            "If no preferences are found, return empty dicts/lists.\n"
            "Return ONLY valid JSON, no explanation.\n\n"
            f"User message: {text[:500]}"
        )
        raw = self._llm_router.invoke_text(instruction, route="classification")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            cleaned = "\n".join(lines[start:end]).strip()

        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            return PreferenceExtraction(prefs_updates={}, soul_updates={}, evidences=[])

        prefs = parsed.get("prefs_updates")
        soul = parsed.get("soul_updates")
        evidences = parsed.get("evidences")

        return PreferenceExtraction(
            prefs_updates=dict(prefs) if isinstance(prefs, dict) else {},
            soul_updates=dict(soul) if isinstance(soul, dict) else {},
            evidences=list(evidences) if isinstance(evidences, list) else [],
        )

    def _extract_with_regex(self, text: str) -> PreferenceExtraction:
        prefs_updates: dict[str, Any] = {}
        soul_updates: dict[str, Any] = {}
        evidences: list[str] = []

        location_match = self._re_default_location.search(text)
        if location_match:
            location = _clean_value(location_match.group(1), max_len=20)
            if location:
                prefs_updates["default_location"] = location
                evidences.append("default_location")

        dislike_match = self._re_dislike.search(text)
        if dislike_match:
            dislike = _clean_value(dislike_match.group(1))
            if dislike:
                prefs_updates["dislike"] = dislike
                evidences.append("dislike")

        like_match = self._re_like.search(text)
        if like_match:
            like = _clean_value(like_match.group(1))
            if like:
                prefs_updates["like"] = like
                evidences.append("like")

        name_match = self._re_name.search(text)
        if name_match:
            name = _clean_value(name_match.group(1), max_len=30)
            if name:
                prefs_updates["preferred_name"] = name
                evidences.append("preferred_name")

        lowered = text.lower()
        if any(token in lowered for token in ("简短", "简洁", "别太啰嗦", "精炼")):
            soul_updates["tone"] = "concise"
            soul_updates["style_rules"] = ["Keep responses concise unless user asks for details."]
            evidences.append("style_concise")
        elif any(token in lowered for token in ("详细一点", "展开讲", "讲细点", "解释详细")):
            soul_updates["tone"] = "detailed"
            soul_updates["style_rules"] = ["Provide more detail with examples when user asks."]
            evidences.append("style_detailed")

        return PreferenceExtraction(
            prefs_updates=prefs_updates,
            soul_updates=soul_updates,
            evidences=evidences,
        )
