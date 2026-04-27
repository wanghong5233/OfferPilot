"""Reward assessment helpers."""

from __future__ import annotations

import json
import logging
from typing import Any

from ....core.llm.router import LLMRouter
from .types import RewardAssessment, TaskResult

logger = logging.getLogger(__name__)


def assess_reward(
    *,
    llm_router: LLMRouter,
    task_result: TaskResult,
    reward_text: str,
    route: str = "classification",
) -> RewardAssessment:
    text = str(reward_text or task_result.reward_text or "").strip()
    if not text:
        return RewardAssessment()
    prompt = (
        "Classify this game reward text as JSON with keys rarity and items. "
        "rarity must be one of common, rare, ssr, limited, special_event.\n"
        f"Task: {task_result.name}\nReward text: {text}"
    )
    parsed = llm_router.invoke_json(prompt, route=route, default=None)
    if not isinstance(parsed, dict):
        return RewardAssessment(raw_text=text)
    rarity = str(parsed.get("rarity") or "common").strip()
    if rarity not in {"common", "rare", "ssr", "limited", "special_event"}:
        rarity = "common"
    raw_items = parsed.get("items")
    items = [str(item) for item in raw_items] if isinstance(raw_items, list) else []
    return RewardAssessment(rarity=rarity, items=items, raw_text=text)


def serialize_assessments(assessments: dict[str, RewardAssessment]) -> dict[str, Any]:
    return json.loads(json.dumps({k: v.to_dict() for k, v in assessments.items()}, ensure_ascii=False))
