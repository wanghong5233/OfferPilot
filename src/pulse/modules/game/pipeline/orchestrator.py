"""Game workflow orchestrator."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ....core.llm.router import LLMRouter
from ....core.memory.archival_memory import ArchivalMemory
from ....core.notify.notifier import Notifier
from .._connectors import GameDriver
from ..games import GameConfig
from ..store import GameRunStore
from .assess import assess_reward
from .capture import capture_screen
from .execute import SafetyGate, execute_task
from .identify import identify_task_action
from .prepare import prepare_game
from .publish import publish_run
from .types import GameRunResult, RewardAssessment, TaskResult
from .verify import verify_task

logger = logging.getLogger(__name__)

StageEventEmitter = Callable[[str, str, dict[str, Any]], None]


class GameWorkflowOrchestrator:
    def __init__(
        self,
        *,
        store: GameRunStore | None,
        llm_router: LLMRouter,
        notifier: Notifier,
        templates_root: Path,
        screenshot_root: Path | None = None,
        archival_memory: ArchivalMemory | None = None,
        emit_stage_event: StageEventEmitter | None = None,
        safety_gate: SafetyGate | None = None,
    ) -> None:
        self._store = store
        self._llm_router = llm_router
        self._notifier = notifier
        self._templates_root = templates_root
        self._screenshot_root = screenshot_root
        self._archival_memory = archival_memory
        self._emit = emit_stage_event
        self._safety_gate = safety_gate

    def set_safety_gate(self, safety_gate: SafetyGate | None) -> None:
        self._safety_gate = safety_gate

    async def run(
        self,
        *,
        game: GameConfig,
        driver: GameDriver,
        dry_run: bool | None = None,
        tasks_filter: list[str] | None = None,
        trace_id: str | None = None,
    ) -> GameRunResult:
        started = datetime.now(timezone.utc)
        clock = time.monotonic()
        run_trace = str(trace_id or "").strip() or None
        run_id = str(uuid.uuid4())
        selected = set(tasks_filter or [])
        effective_dry_run = game.safety.default_dry_run if dry_run is None else bool(dry_run)

        self._fire(
            "workflow",
            "started",
            run_trace,
            {"game_id": game.id, "dry_run": effective_dry_run, "tasks_filter": list(selected)},
        )
        prepare = prepare_game(
            driver,
            game,
            templates_root=self._templates_root,
            dry_run=effective_dry_run,
        )
        self._fire("prepare", "completed" if prepare.get("ok") else "failed", run_trace, prepare)
        if not prepare.get("ok"):
            return self._publish_not_ready(
                game=game,
                status=str(prepare.get("status") or "not_ready"),
                dry_run=effective_dry_run,
                started_at=started,
                trace_id=run_trace,
                error_payload=prepare,
            )

        results: list[TaskResult] = []
        assessments: dict[str, RewardAssessment] = {}
        for task in game.tasks:
            if selected and task.name not in selected:
                continue
            if time.monotonic() - clock > game.safety.max_total_seconds:
                results.append(
                    TaskResult(
                        name=task.name,
                        task_type=task.type,
                        status="failed",
                        error="workflow_timeout",
                        error_message="Game workflow exceeded max_total_seconds.",
                    )
                )
                break
            try:
                screenshot = capture_screen(driver)
                screenshot.ref = self._save_screenshot(
                    game_id=game.id,
                    run_id=run_id,
                    task_name=task.name,
                    phase="before",
                    image_bytes=screenshot.image_bytes,
                )
            except RuntimeError as exc:
                result = TaskResult(
                    name=task.name,
                    task_type=task.type,
                    status="failed",
                    error="capture_failed",
                    error_message=str(exc),
                )
                results.append(result)
                self._fire("exception", "failed", run_trace, {"game_id": game.id, "kind": "driver_lost", "error": str(exc)})
                break
            self._fire(
                "capture",
                "completed",
                run_trace,
                {"game_id": game.id, "task": task.name, "screenshot_ref": screenshot.ref},
            )

            action = identify_task_action(
                driver=driver,
                game=game,
                task=task,
                screenshot=screenshot,
                templates_root=self._templates_root,
            )
            if not action.get("ok"):
                action = self._try_vision_fallback(
                    game=game,
                    task=task,
                    screenshot=screenshot,
                    trace_id=run_trace,
                )
            if not action.get("ok"):
                result = TaskResult(
                    name=task.name,
                    task_type=task.type,
                    status="unknown_screen",
                    error=str(action.get("error") or "unknown_screen"),
                    error_message=str(action.get("error_message") or "Unknown screen."),
                    screenshot_after_ref=screenshot.ref,
                    metadata={"action": action, "screenshot_ref": screenshot.ref},
                )
                results.append(result)
                self._fire(
                    "exception",
                    "failed",
                    run_trace,
                    {
                        "game_id": game.id,
                        "kind": "unknown_screen",
                        "task": task.name,
                        "action": action,
                        "screenshot_ref": screenshot.ref,
                    },
                )
                continue
            self._fire("identify", "completed", run_trace, {"game_id": game.id, "task": task.name, "action": action})

            try:
                executed = execute_task(
                    driver=driver,
                    game=game,
                    task=task,
                    action=action,
                    screenshot=screenshot,
                    templates_root=self._templates_root,
                    dry_run=effective_dry_run,
                    safety_gate=self._safety_gate,
                )
                verified = verify_task(driver, executed)
            except RuntimeError as exc:
                verified = TaskResult(
                    name=task.name,
                    task_type=task.type,
                    status="failed",
                    error="driver_lost",
                    error_message=str(exc),
                )
            results.append(verified)
            self._fire("execute", "completed" if verified.succeeded else "failed", run_trace, verified.to_dict())
            self._fire("verify", "completed", run_trace, {"game_id": game.id, "task": task.name, "status": verified.status})
            assessment = self._assess_verified_reward(
                driver=driver,
                task_result=verified,
                trace_id=run_trace,
                game_id=game.id,
            )
            if assessment is not None:
                assessments[verified.name] = assessment

        status = _derive_status(results)
        finished = datetime.now(timezone.utc)
        result = GameRunResult(
            game_id=game.id,
            status=status,
            tasks=results,
            rewards_summary="",
            dry_run=effective_dry_run,
            started_at=started,
            finished_at=finished,
            run_id=run_id,
        )
        published = publish_run(
            game=game,
            result=result,
            store=self._store,
            notifier=self._notifier,
            llm_router=self._llm_router,
            assessments=assessments,
            archival_memory=self._archival_memory,
        )
        self._fire(
            "workflow",
            "completed",
            run_trace,
            {"game_id": game.id, "status": published.status, "elapsed_ms": int((time.monotonic() - clock) * 1000)},
        )
        return published

    def _publish_not_ready(
        self,
        *,
        game: GameConfig,
        status: str,
        dry_run: bool,
        started_at: datetime,
        trace_id: str | None,
        error_payload: dict[str, Any],
    ) -> GameRunResult:
        task = TaskResult(
            name="prepare",
            task_type="prepare",
            status="failed",
            error=str(error_payload.get("error") or status),
            error_message=str(error_payload.get("error_message") or status),
            metadata=error_payload,
        )
        result = GameRunResult(
            game_id=game.id,
            status=status,
            tasks=[task],
            rewards_summary="",
            dry_run=dry_run,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
        published = publish_run(
            game=game,
            result=result,
            store=self._store,
            notifier=self._notifier,
            llm_router=self._llm_router,
            assessments={},
            archival_memory=self._archival_memory,
        )
        self._fire("workflow", "failed", trace_id, {"game_id": game.id, "status": status, "error": task.error})
        return published

    def _fire(
        self,
        stage: str,
        status: str,
        trace_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        if self._emit is None:
            return
        event_payload = dict(payload)
        if trace_id:
            event_payload["trace_id"] = trace_id
        self._emit(stage, status, event_payload)

    def _save_screenshot(
        self,
        *,
        game_id: str,
        run_id: str,
        task_name: str,
        phase: str,
        image_bytes: bytes,
    ) -> str:
        if self._screenshot_root is None:
            return ""
        safe_task = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in task_name)
        safe_phase = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in phase)
        target_dir = self._screenshot_root / game_id / run_id
        target = target_dir / f"{safe_task}_{safe_phase}.png"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image_bytes)
        except OSError as exc:
            logger.warning("game screenshot save failed path=%s err=%s", target, exc)
            return ""
        return str(target)

    def _try_vision_fallback(
        self,
        *,
        game: GameConfig,
        task: Any,
        screenshot: Any,
        trace_id: str | None,
    ) -> dict[str, Any]:
        candidates = _vision_candidates(task)
        allowlist = ["give_up", *[str(item["action_id"]) for item in candidates]]
        prompt = (
            "You are identifying a mobile game screen. Choose exactly one action_id "
            "from the allowlist and return JSON: {\"action_id\": string, \"confidence\": number, \"reason\": string}. "
            "Do not output coordinates.\n"
            f"game_id={game.id}\ntask={task.name}\nallowlist={allowlist}"
        )
        parsed = self._llm_router.invoke_vision_json(
            prompt,
            [screenshot.image_bytes],
            route="vision",
            default=None,
        )
        if not isinstance(parsed, dict):
            self._fire("exception", "failed", trace_id, {"game_id": game.id, "kind": "llm_failed", "task": task.name})
            return {"ok": False, "status": "unknown_screen", "error": "vision_no_result"}
        action_id = str(parsed.get("action_id") or "").strip()
        if action_id not in allowlist or action_id == "give_up":
            return {
                "ok": False,
                "status": "unknown_screen",
                "error": "vision_give_up",
                "error_message": str(parsed.get("reason") or "Vision model did not select an executable action."),
                "vision": parsed,
            }
        selected = next((item for item in candidates if item["action_id"] == action_id), None)
        if selected is None:
            return {"ok": False, "status": "unknown_screen", "error": "vision_action_unmapped", "vision": parsed}
        return {
            "ok": True,
            "source": "vision",
            "action_type": "tap",
            "action_id": action_id,
            "x": int(selected["x"]),
            "y": int(selected["y"]),
            "vision": parsed,
        }

    def _assess_verified_reward(
        self,
        *,
        driver: GameDriver,
        task_result: TaskResult,
        trace_id: str | None,
        game_id: str,
    ) -> RewardAssessment | None:
        if not task_result.succeeded or task_result.status == "dry_run":
            return None
        screenshot = driver.screenshot()
        if not screenshot.get("ok") or not isinstance(screenshot.get("image_bytes"), bytes):
            return None
        ocr = driver.find_text(image_bytes=screenshot["image_bytes"], query="")
        if not ocr.get("ok"):
            return None
        text = str(ocr.get("text") or "").strip()
        if not text:
            return None
        assessment = assess_reward(
            llm_router=self._llm_router,
            task_result=task_result,
            reward_text=text,
        )
        self._fire(
            "assess",
            "completed",
            trace_id,
            {"game_id": game_id, "task": task_result.name, "rarity": assessment.rarity, "items": assessment.items},
        )
        return assessment


def _derive_status(results: list[TaskResult]) -> str:
    if not results:
        return "not_ready"
    success_count = sum(1 for result in results if result.succeeded)
    if success_count == len(results):
        return "success"
    if success_count > 0:
        return "partial"
    return "failed"


def _vision_candidates(task: Any) -> list[dict[str, Any]]:
    raw_actions = getattr(task, "params", {}).get("vision_actions") or []
    candidates: list[dict[str, Any]] = []
    if not isinstance(raw_actions, list):
        return candidates
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("action_id") or "").strip()
        if not action_id:
            continue
        try:
            x = int(item["x"])
            y = int(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        candidates.append({"action_id": action_id, "x": x, "y": y})
    return candidates
