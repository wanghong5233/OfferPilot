"""GameModule — low-frequency game automation workflow."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core.llm.router import LLMRouter
from ...core.memory.archival_memory import ArchivalMemory
from ...core.module import BaseModule, IntentSpec
from ...core.notify.notifier import ConsoleNotifier, FeishuNotifier, MultiNotifier, Notifier
from ...core.notify.notifier import Notification
from ...core.safety import (
    SAFETY_PLANE_OFF,
    Intent,
    PermissionContext,
    ResumedExecution,
    ResumedTaskExecutor,
    SuspendedTask,
    SuspendedTaskStore,
    gacha_policy,
    render_ask_for_im,
)
from ...core.storage.engine import DatabaseEngine
from ...core.task_context import TaskContext
from ._connectors import build_driver
from .config import GameSettings, get_game_settings
from .games import GameConfig, load_game_configs
from .intent import build_game_intents
from .pipeline import GameWorkflowOrchestrator
from .store import GameRunStore

logger = logging.getLogger(__name__)

_GAMES_DIR = Path(__file__).parent / "games"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

try:
    from psycopg import Error as PsycopgError
except ImportError:  # pragma: no cover - exercised without db extra
    PsycopgError = RuntimeError  # type: ignore[misc, assignment]


class GameRunRequest(BaseModel):
    dry_run: bool | None = None
    tasks_filter: list[str] | None = None


class GameRunsListRequest(BaseModel):
    game_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class GameModule(BaseModule):
    name = "game"
    description = (
        "Low-frequency game automation module. Runs deterministic workflows "
        "declared by game YAMLs, with LLM only used for bounded fallback steps."
    )
    route_prefix = "/api/modules/game"
    tags = ["game"]

    def __init__(
        self,
        *,
        games_dir: Path | None = None,
        settings: GameSettings | None = None,
        store: GameRunStore | None = None,
        llm_router: LLMRouter | None = None,
        notifier: Notifier | None = None,
        archival_memory: ArchivalMemory | None = None,
    ) -> None:
        super().__init__()
        self._games_dir = games_dir or _GAMES_DIR
        self._settings = settings or get_game_settings()
        self._templates_root = Path(self._settings.templates_dir) if self._settings.templates_dir else _TEMPLATES_DIR
        self._screenshot_root = (
            Path(self._settings.screenshot_dir)
            if self._settings.screenshot_dir
            else Path.home() / ".pulse" / "game" / "screenshots"
        )
        self._games: dict[str, GameConfig] = {}
        self._load_games()
        self._store: GameRunStore | None = store or self._build_store()
        self._llm_router = llm_router or LLMRouter()
        self._notifier: Notifier = notifier or MultiNotifier([ConsoleNotifier(), FeishuNotifier()])
        self._archival_memory = archival_memory if archival_memory is not None else self._build_archival_memory()
        self._suspended_store: SuspendedTaskStore | None = None
        self._safety_workspace_id = "default"
        self._safety_mode = SAFETY_PLANE_OFF
        self._resume_approval_tokens: set[str] = set()
        self._orchestrator = GameWorkflowOrchestrator(
            store=self._store,
            llm_router=self._llm_router,
            notifier=self._notifier,
            templates_root=self._templates_root,
            screenshot_root=self._screenshot_root,
            archival_memory=self._archival_memory,
            emit_stage_event=self._emit_pipeline_event,
            safety_gate=self._evaluate_safety,
        )
        self.intents: list[IntentSpec] = build_game_intents(self)

    def _load_games(self) -> None:
        if not self._games_dir.is_dir():
            logger.warning("game configs dir missing: %s", self._games_dir)
            self._games = {}
            return
        configs = load_game_configs(self._games_dir)
        self._games = {config.id: config for config in configs}
        logger.info("game module loaded %d game(s): %s", len(self._games), sorted(self._games))

    def _build_store(self) -> GameRunStore | None:
        try:
            store = GameRunStore(db_engine=DatabaseEngine())
        except RuntimeError as exc:
            logger.warning("game module starting without DB engine: %s", exc)
            return None
        try:
            store.ensure_schema()
        except RuntimeError as exc:
            logger.error("game store schema check failed: %s", exc)
            raise
        except PsycopgError as exc:
            logger.warning("game store unavailable, deferring schema init: %s", exc)
        return store

    def _build_archival_memory(self) -> ArchivalMemory | None:
        try:
            return ArchivalMemory()
        except (RuntimeError, PsycopgError) as exc:
            logger.warning("game module starting without ArchivalMemory promotion: %s", exc)
            return None

    def _emit_pipeline_event(self, stage: str, status: str, payload: dict[str, Any]) -> None:
        trace_id = str(payload.get("trace_id") or "").strip() or None
        clean_payload = {k: v for k, v in payload.items() if k != "trace_id"}
        self.emit_stage_event(
            stage=stage,
            status=status,
            trace_id=trace_id,
            payload=clean_payload,
        )

    def on_startup(self) -> None:
        if self._runtime is None:
            return
        for game in self._games.values():
            self._register_game_patrol(game)

    def attach_safety_plane(
        self,
        *,
        suspended_store: SuspendedTaskStore,
        workspace_id: str,
        mode: str,
    ) -> None:
        self._suspended_store = suspended_store
        self._safety_workspace_id = str(workspace_id or "default")
        self._safety_mode = str(mode or SAFETY_PLANE_OFF)
        self._orchestrator.set_safety_gate(self._evaluate_safety)

    def get_resumed_task_executor(self) -> ResumedTaskExecutor | None:
        def _executor(
            *,
            task: SuspendedTask,
            user_answer: str,
        ) -> ResumedExecution:
            return self._resume_suspended_task(task=task, user_answer=user_answer)

        return _executor

    def _register_game_patrol(self, game: GameConfig) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.register_patrol(
            name=game.patrol_name,
            handler=self._make_patrol_handler(game.id),
            peak_interval=game.schedule.peak_interval_seconds,
            offpeak_interval=game.schedule.offpeak_interval_seconds,
            enabled=game.schedule.enabled_by_default,
            active_hours_only=game.schedule.active_hours_only,
            weekday_windows=tuple(game.schedule.weekday_windows),
            weekend_windows=tuple(game.schedule.weekend_windows),
        )
        logger.info("game patrol registered game=%s enabled=%s", game.id, game.schedule.enabled_by_default)

    def _make_patrol_handler(self, game_id: str):
        def _handler(ctx: TaskContext) -> dict[str, Any]:
            _ = ctx
            return _run_async(self.run_workflow(game_id=game_id))

        return _handler

    def _evaluate_safety(self, game: GameConfig, task: Any) -> dict[str, Any]:
        if self._safety_mode == SAFETY_PLANE_OFF:
            return {"ok": True, "status": "allowed", "reason": "safety_plane_off"}
        if getattr(task, "type", "") != "gacha":
            return {"ok": True, "status": "allowed", "reason": "non_gacha_task"}

        mode = str(task.params.get("mode") or "").strip()
        task_name = str(task.name)
        approval_token = f"game:{game.id}:{task_name}"
        if approval_token in self._resume_approval_tokens:
            return {"ok": True, "status": "allowed", "reason": "resume_approved"}

        used_today = 0
        if self._store is not None:
            used_today = self._store.count_task_today(game_id=game.id, task_name=task_name)
        intent = Intent(
            kind="mutation",
            name=f"game.{game.id}.gacha",
            args={
                "game_id": game.id,
                "task_name": task_name,
                "mode": mode,
                "daily_max_pulls": int(task.params.get("daily_max_pulls") or 0),
                "used_today": used_today,
            },
            evidence_keys=(),
        )
        ctx = PermissionContext(
            module="game",
            task_id=approval_token,
            trace_id=f"game:{game.id}",
            user_id=None,
        )
        decision = gacha_policy(intent, ctx)
        if decision.kind == "allow":
            return {"ok": True, "status": "allowed", "reason": decision.reason, "rule_id": decision.rule_id}
        if decision.kind == "deny":
            return {
                "ok": False,
                "status": "denied",
                "error": decision.deny_code or "policy_denied",
                "error_message": decision.reason,
                "rule_id": decision.rule_id,
            }
        return self._suspend_gacha(intent=intent, decision=decision, game=game, task_name=task_name)

    def _suspend_gacha(
        self,
        *,
        intent: Intent,
        decision: Any,
        game: GameConfig,
        task_name: str,
    ) -> dict[str, Any]:
        store = self._suspended_store
        ask_request = decision.ask_request
        if store is None or ask_request is None:
            return {
                "ok": False,
                "status": "denied",
                "error": "safety_store_unavailable",
                "error_message": "SafetyPlane enforce mode is active but suspended task store is unavailable.",
            }
        new_task_id = f"safety_{uuid4().hex[:12]}"
        try:
            task = store.create(
                task_id=new_task_id,
                module="game",
                trace_id=ask_request.resume_handle.task_id,
                workspace_id=self._safety_workspace_id,
                intent=intent,
                ask_request=ask_request,
                origin_rule_id=decision.rule_id,
                origin_decision_reason=decision.reason,
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.warning("game safety suspend failed game=%s task=%s err=%s", game.id, task_name, exc)
            return {
                "ok": False,
                "status": "denied",
                "error": "safety_suspend_failed",
                "error_message": str(exc),
            }
        if task.task_id == new_task_id:
            ask_text = render_ask_for_im(ask_request, channel="feishu")
            self._notifier.send(
                Notification(
                    level="warn",
                    title="Pulse 需要你确认游戏抽卡",
                    content=ask_text,
                    metadata={"task_id": task.task_id, "game_id": game.id, "task_name": task_name},
                )
            )
        return {
            "ok": False,
            "status": "suspended",
            "error": "requires_user_confirmation",
            "error_message": decision.reason,
            "task_id": task.task_id,
            "rule_id": decision.rule_id,
        }

    def _resume_suspended_task(self, *, task: SuspendedTask, user_answer: str) -> ResumedExecution:
        answer = str(user_answer or "").strip().lower()
        if answer in {"n", "no", "否", "不", "取消"}:
            return ResumedExecution(
                status="declined",
                ok=True,
                summary="已取消游戏抽卡操作。",
                detail={"task_id": task.task_id},
            )
        if answer not in {"y", "yes", "是", "确认", "同意"}:
            return ResumedExecution(
                status="undetermined",
                ok=False,
                summary="未识别为确认或拒绝,本次不执行游戏抽卡。",
                detail={"task_id": task.task_id},
            )
        args = dict(task.original_intent.args)
        game_id = str(args.get("game_id") or "")
        task_name = str(args.get("task_name") or "")
        if not game_id or not task_name:
            return ResumedExecution(
                status="failed",
                ok=False,
                summary="挂起任务缺少 game_id 或 task_name,无法恢复执行。",
                detail={"task_id": task.task_id, "args": args},
            )
        token = f"game:{game_id}:{task_name}"
        self._resume_approval_tokens.add(token)
        try:
            result = _run_async(
                self.run_workflow(
                    game_id=game_id,
                    dry_run=False,
                    tasks_filter=[task_name],
                )
            )
        except RuntimeError as exc:
            return ResumedExecution(
                status="failed",
                ok=False,
                summary=f"游戏抽卡恢复执行失败: {exc}",
                detail={"task_id": task.task_id, "error": str(exc)},
            )
        finally:
            self._resume_approval_tokens.discard(token)
        return ResumedExecution(
            status="executed" if result.get("ok") else "failed",
            ok=bool(result.get("ok")),
            summary=str(result.get("rewards_summary") or "游戏抽卡恢复执行完成。"),
            detail={"task_id": task.task_id, "result": result},
        )

    async def run_workflow(
        self,
        *,
        game_id: str,
        dry_run: bool | None = None,
        tasks_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        game = self._games.get(game_id)
        if game is None:
            return {"ok": False, "error": "game_not_found", "game_id": game_id}
        driver = build_driver(
            game.driver,
            settings=self._settings,
            templates_root=self._templates_root / game.templates_dir,
        )
        result = await self._orchestrator.run(
            game=game,
            driver=driver,
            dry_run=dry_run,
            tasks_filter=tasks_filter,
        )
        return result.to_dict()

    def list_runs(self, *, game_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "store_unavailable", "runs": []}
        return {"ok": True, "runs": self._store.list_recent(game_id=game_id, limit=limit)}

    def latest_run(self, *, game_id: str) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "store_unavailable", "run": None}
        return {"ok": True, "run": self._store.latest(game_id=game_id)}

    def register_routes(self, router: APIRouter) -> None:
        @router.get("/health")
        def health() -> dict[str, Any]:
            return {
                "ok": True,
                "games": sorted(self._games),
                "store_ready": self._store is not None,
                "templates_root": str(self._templates_root),
                "screenshot_root": str(self._screenshot_root),
            }

        @router.get("/games")
        def list_games() -> dict[str, Any]:
            return {
                "ok": True,
                "games": [
                    {
                        "id": game.id,
                        "name": game.name,
                        "driver": game.driver,
                        "tasks": [task.name for task in game.tasks],
                        "patrol_name": game.patrol_name,
                    }
                    for game in self._games.values()
                ],
            }

        @router.post("/games/{game_id}/run")
        async def run_game(game_id: str, request: GameRunRequest) -> dict[str, Any]:
            result = await self.run_workflow(
                game_id=game_id,
                dry_run=request.dry_run,
                tasks_filter=request.tasks_filter,
            )
            if not result.get("ok") and result.get("error") == "game_not_found":
                raise HTTPException(status_code=404, detail=result)
            return result

        @router.get("/runs")
        def runs(game_id: str | None = None, limit: int = 20) -> dict[str, Any]:
            return self.list_runs(game_id=game_id, limit=limit)

        @router.get("/runs/{game_id}/latest")
        def latest(game_id: str) -> dict[str, Any]:
            return self.latest_run(game_id=game_id)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("GameModule patrol handler cannot call asyncio.run inside a running event loop")


def get_module() -> GameModule:
    return GameModule()
