from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..learning.preference_extractor import PreferenceExtractor


def _is_correction_text(text: str) -> bool:
    safe = str(text or "").strip().lower()
    if not safe:
        return False
    tokens = (
        "你错了",
        "不对",
        "纠正",
        "应该",
        "以后",
        "默认",
        "不要再",
        "我喜欢",
        "我不喜欢",
    )
    return any(token in safe for token in tokens)


@dataclass(slots=True)
class EvolutionResult:
    classification: str
    preference_applied: list[dict[str, Any]]
    soul_applied: list[dict[str, Any]]
    belief_applied: list[dict[str, Any]]
    archival_facts: list[dict[str, Any]]
    dpo_collected: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "preference_applied": list(self.preference_applied),
            "soul_applied": list(self.soul_applied),
            "belief_applied": list(self.belief_applied),
            "archival_facts": list(self.archival_facts),
            "dpo_collected": dict(self.dpo_collected) if isinstance(self.dpo_collected, dict) else None,
        }


class SoulEvolutionEngine:
    """Reflection pipeline: classify -> extract -> govern -> archive."""

    def __init__(
        self,
        *,
        governance: Any,
        archival_memory: Any,
        preference_extractor: PreferenceExtractor | None = None,
        dpo_collector: Any | None = None,
        dpo_auto_collect: bool = True,
    ) -> None:
        self._governance = governance
        self._archival_memory = archival_memory
        self._extractor = preference_extractor or PreferenceExtractor()
        self._dpo_collector = dpo_collector
        self._dpo_auto_collect = bool(dpo_auto_collect)

    def reflect_interaction(
        self,
        *,
        user_text: str,
        assistant_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> EvolutionResult:
        _ = assistant_text
        safe_user_text = str(user_text or "").strip()
        safe_metadata = dict(metadata or {})

        classification = "correction" if _is_correction_text(safe_user_text) else "regular"
        extracted = self._extractor.extract(safe_user_text)

        preference_applied: list[dict[str, Any]] = []
        soul_applied: list[dict[str, Any]] = []
        belief_applied: list[dict[str, Any]] = []
        archival_facts: list[dict[str, Any]] = []
        dpo_collected: dict[str, Any] | None = None

        if extracted.prefs_updates:
            prefs_risk = self._infer_pref_risk(extracted.prefs_updates)
            pref_result = self._governance.apply_preference_updates(
                updates=extracted.prefs_updates,
                source=f"evolution:{classification}",
                risk_level=prefs_risk,
            )
            preference_applied.append(pref_result)
            if pref_result.get("ok"):
                for key, value in extracted.prefs_updates.items():
                    fact = self._archival_memory.add_fact(
                        subject="user",
                        predicate=f"preference.{key}",
                        object_value=value,
                        source="preference_extractor",
                        confidence=0.9,
                        metadata={"classification": classification, "metadata": safe_metadata},
                    )
                    archival_facts.append(fact)
                belief_text = ", ".join(f"{k}={v}" for k, v in extracted.prefs_updates.items())
                belief_result = self._governance.add_mutable_belief(
                    belief=f"User preference updated: {belief_text}",
                    source="evolution_reflection",
                    risk_level="low",
                )
                belief_applied.append(belief_result)

        if extracted.soul_updates:
            soul_risk = self._infer_soul_risk(extracted.soul_updates)
            soul_result = self._governance.apply_soul_update(
                updates=extracted.soul_updates,
                source=f"evolution:{classification}",
                risk_level=soul_risk,
            )
            soul_applied.append(soul_result)
            if soul_result.get("ok"):
                for key, value in extracted.soul_updates.items():
                    fact = self._archival_memory.add_fact(
                        subject="assistant",
                        predicate=f"soul.{key}",
                        object_value=value,
                        source="soul_governance",
                        confidence=0.7,
                        metadata={"classification": classification, "metadata": safe_metadata},
                    )
                    archival_facts.append(fact)

        if self._dpo_collector is not None and classification == "correction":
            collect_dpo_raw = safe_metadata.get("collect_dpo")
            collect_dpo = self._dpo_auto_collect if collect_dpo_raw is None else bool(collect_dpo_raw)
            if collect_dpo:
                chosen_raw = safe_metadata.get("dpo_chosen")
                rejected_raw = safe_metadata.get("dpo_rejected")
                chosen = str(chosen_raw).strip() if isinstance(chosen_raw, str) else ""
                rejected = str(rejected_raw).strip() if isinstance(rejected_raw, str) else ""
                if not chosen:
                    chosen = f"Follow user correction: {safe_user_text[:300]}"
                if not rejected:
                    rejected = str(assistant_text or "").strip() or "N/A"
                try:
                    dpo_collected = self._dpo_collector.add_pair(
                        prompt=safe_user_text,
                        chosen=chosen,
                        rejected=rejected,
                        metadata={"source": "evolution_reflection", "metadata": safe_metadata},
                    )
                except Exception:
                    dpo_collected = None

        return EvolutionResult(
            classification=classification,
            preference_applied=preference_applied,
            soul_applied=soul_applied,
            belief_applied=belief_applied,
            archival_facts=archival_facts,
            dpo_collected=dpo_collected,
        )

    @staticmethod
    def _infer_pref_risk(updates: dict[str, Any]) -> str:
        keys = {str(key).strip().lower() for key in updates.keys()}
        if not keys:
            return "low"
        if "default_location" in keys and len(keys) == 1:
            return "low"
        if "preferred_name" in keys and len(keys) == 1:
            return "low"
        if "like" in keys or "dislike" in keys:
            return "medium"
        if len(keys) >= 3:
            return "high"
        return "medium"

    @staticmethod
    def _infer_soul_risk(updates: dict[str, Any]) -> str:
        keys = {str(key).strip().lower() for key in updates.keys()}
        if not keys:
            return "medium"
        if "style_rules" in keys:
            return "high"
        return "medium"
