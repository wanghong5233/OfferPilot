"""Publish stage for Game workflow."""

from __future__ import annotations

import logging

from ....core.llm.router import LLMRouter
from ....core.memory.archival_memory import ArchivalMemory
from ....core.notify.notifier import Notification, Notifier
from ....core.tokenizer import token_preview
from ..games import GameConfig
from ..store import GameRunRecord, GameRunStore
from .types import GameRunResult, RewardAssessment

logger = logging.getLogger(__name__)

try:
    from psycopg import Error as PsycopgError
except ImportError:  # pragma: no cover - exercised without db extra
    PsycopgError = RuntimeError  # type: ignore[misc, assignment]


def publish_run(
    *,
    game: GameConfig,
    result: GameRunResult,
    store: GameRunStore | None,
    notifier: Notifier,
    llm_router: LLMRouter,
    assessments: dict[str, RewardAssessment] | None = None,
    archival_memory: ArchivalMemory | None = None,
) -> GameRunResult:
    result.rewards_summary = _build_summary(llm_router=llm_router, result=result)
    if archival_memory is not None and assessments:
        result.promoted_to_archival = _promote_rewards(
            archival_memory=archival_memory,
            game=game,
            result=result,
            assessments=assessments,
        )
    if store is not None:
        store.append(
            GameRunRecord(
                id=result.run_id,
                game_id=result.game_id,
                status=result.status,
                tasks=[task.to_dict() for task in result.tasks],
                rewards_summary=result.rewards_summary,
                dry_run=result.dry_run,
                promoted_to_archival=result.promoted_to_archival,
                started_at=result.started_at,
                finished_at=result.finished_at,
            )
        )
    notifier.send(
        Notification(
            level="info" if result.status in {"success", "partial"} else "warning",
            title=f"Game workflow: {game.name}",
            content=result.rewards_summary,
            metadata={"game_id": game.id, "run_id": result.run_id, "status": result.status},
        )
    )
    return result


def _build_summary(*, llm_router: LLMRouter, result: GameRunResult) -> str:
    total = len(result.tasks)
    succeeded = sum(1 for task in result.tasks if task.succeeded)
    fallback = f"{result.game_id}: 完成 {succeeded}/{total} 个任务,status={result.status}"
    tasks_preview = token_preview(
        str([task.to_dict() for task in result.tasks]),
        max_tokens=900,
    )
    prompt = (
        "用一句简短中文总结这次游戏日常自动化结果,不要夸张,不要输出 Markdown。\n"
        f"game_id={result.game_id}\nstatus={result.status}\n"
        f"tasks={tasks_preview}"
    )
    try:
        text = llm_router.invoke_text(prompt, route="generation").strip()
    except RuntimeError as exc:
        logger.warning("game rewards_summary LLM failed: %s", exc)
        return fallback
    return text or fallback


def _promote_rewards(
    *,
    archival_memory: ArchivalMemory,
    game: GameConfig,
    result: GameRunResult,
    assessments: dict[str, RewardAssessment],
) -> bool:
    promoted = False
    promote_on = set(game.publish.promote_archival_on)
    for task_name, assessment in assessments.items():
        if assessment.rarity not in promote_on:
            continue
        try:
            archival_memory.add_fact(
                subject=f"game:{game.id}",
                predicate="rare_pull" if "gacha" in task_name else "rare_drop",
                object_value={
                    "items": assessment.items,
                    "rarity": assessment.rarity,
                    "task": task_name,
                    "run_id": result.run_id,
                    "time": result.finished_at.isoformat(),
                },
                source="game_module",
                evidence_refs=[result.run_id],
                run_id=result.run_id,
            )
            promoted = True
        except (RuntimeError, ValueError, TypeError, PsycopgError) as exc:
            logger.warning(
                "game archival promotion failed game=%s task=%s rarity=%s err=%s",
                game.id,
                task_name,
                assessment.rarity,
                exc,
            )
    return promoted
