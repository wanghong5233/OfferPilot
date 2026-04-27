"""IntentSpec wiring for the Game module."""

from __future__ import annotations

from typing import Any, Protocol

from ...core.module import IntentSpec


class GameService(Protocol):
    async def run_workflow(
        self,
        *,
        game_id: str,
        dry_run: bool | None = None,
        tasks_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        ...

    def list_runs(self, *, game_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        ...

    def latest_run(self, *, game_id: str) -> dict[str, Any]:
        ...


def build_game_intents(service: GameService) -> list[IntentSpec]:
    return [
        IntentSpec(
            name="game.workflow.run",
            description="Run one configured game workflow now. Supports dry_run and task filtering.",
            when_to_use="用户要求立刻执行游戏日常、签到、领取月卡、邮件奖励、任务图奖励或抽卡。",
            when_not_to_use="只查看历史结果时用 game.runs.latest 或 game.runs.list。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "game_id": {
                        "type": "string",
                        "description": "Game id from YAML, e.g. shuailu_zhibin.",
                        "default": "shuailu_zhibin",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, do not tap the device or consume resources.",
                    },
                    "tasks_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional task names to run.",
                    },
                },
                "required": ["game_id"],
                "additionalProperties": False,
            },
            handler=_wrap_run(service),
            mutates=True,
            risk_level=1,
            examples=[
                {"user_utterance": "先 dry run 跑一遍率土之滨日常", "kwargs": {"game_id": "shuailu_zhibin", "dry_run": True}},
            ],
        ),
        IntentSpec(
            name="game.runs.list",
            description="List recent game workflow runs.",
            when_to_use="用户想看最近几次游戏自动化执行记录。",
            when_not_to_use="想立刻执行任务时用 game.workflow.run。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "game_id": {"type": "string", "description": "Optional game id."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "additionalProperties": False,
            },
            handler=_wrap_list(service),
            mutates=False,
            risk_level=0,
        ),
        IntentSpec(
            name="game.runs.latest",
            description="Return the latest run for one game.",
            when_to_use="用户问某个游戏今天是否已经跑过或上次结果是什么。",
            when_not_to_use="想看多次历史记录时用 game.runs.list。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "game_id": {
                        "type": "string",
                        "description": "Game id from YAML.",
                        "default": "shuailu_zhibin",
                    }
                },
                "required": ["game_id"],
                "additionalProperties": False,
            },
            handler=_wrap_latest(service),
            mutates=False,
            risk_level=0,
        ),
    ]


def _wrap_run(service: GameService):
    async def _handler(
        game_id: str = "shuailu_zhibin",
        dry_run: bool | None = None,
        tasks_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        return await service.run_workflow(
            game_id=game_id,
            dry_run=dry_run,
            tasks_filter=tasks_filter,
        )

    return _handler


def _wrap_list(service: GameService):
    def _handler(game_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return service.list_runs(game_id=game_id, limit=limit)

    return _handler


def _wrap_latest(service: GameService):
    def _handler(game_id: str = "shuailu_zhibin") -> dict[str, Any]:
        return service.latest_run(game_id=game_id)

    return _handler
