from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pulse.modules.game.games import GameConfig
from pulse.modules.game.pipeline import GameWorkflowOrchestrator
from pulse.modules.game.pipeline.publish import publish_run
from pulse.modules.game.pipeline.types import GameRunResult, RewardAssessment, TaskResult


class _FakeDriver:
    provider_name = "fake_game"

    def __init__(self, *, missing_templates: set[str] | None = None) -> None:
        self.missing_templates = missing_templates or set()
        self.taps: list[tuple[int, int]] = []

    @property
    def execution_ready(self) -> bool:
        return True

    def health(self) -> dict[str, Any]:
        return {"ok": True, "source": self.provider_name}

    def installed_packages(self) -> dict[str, Any]:
        return {"ok": True, "source": self.provider_name, "packages": ["com.test.game"]}

    def app_in_foreground(self, *, package_name: str) -> dict[str, Any]:
        return {"ok": True, "source": self.provider_name, "foreground": package_name == "com.test.game"}

    def screenshot(self) -> dict[str, Any]:
        return {"ok": True, "source": self.provider_name, "image_bytes": b"screen"}

    def find_template(self, *, image_bytes: bytes, template_path: str, threshold: float = 0.9) -> dict[str, Any]:
        _ = image_bytes, threshold
        name = Path(template_path).name
        if name in self.missing_templates:
            return {
                "ok": False,
                "source": self.provider_name,
                "error": "template_not_found",
                "error_message": name,
            }
        return {
            "ok": True,
            "source": self.provider_name,
            "found": True,
            "x": 11,
            "y": 22,
            "score": 0.99,
        }

    def tap(self, *, x: int, y: int) -> dict[str, Any]:
        self.taps.append((x, y))
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> dict[str, Any]:
        _ = x1, y1, x2, y2, duration_ms
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def text(self, *, value: str) -> dict[str, Any]:
        _ = value
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def find_text(self, *, image_bytes: bytes, query: str) -> dict[str, Any]:
        _ = image_bytes, query
        return {"ok": True, "source": self.provider_name, "found": True, "text": ""}


class _FakeLLM:
    def __init__(self, *, vision_action_id: str = "give_up") -> None:
        self.vision_action_id = vision_action_id

    def invoke_text(self, prompt: str, *, route: str = "default") -> str:
        _ = prompt, route
        return "完成测试游戏日常。"

    def invoke_json(self, prompt: str, *, route: str = "default", default: Any = None) -> Any:
        _ = prompt, route
        return default

    def invoke_vision_json(
        self,
        prompt: str,
        images: list[bytes],
        *,
        route: str = "vision",
        default: Any = None,
    ) -> Any:
        _ = prompt, images, route, default
        return {"action_id": self.vision_action_id, "confidence": 0.8, "reason": "test"}


class _RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message: Any) -> None:
        self.sent.append(message)


def _game() -> GameConfig:
    return GameConfig.model_validate(
        {
            "id": "test_game",
            "name": "测试游戏",
            "package_candidates": ["com.test.game"],
            "driver": "adb_airtest",
            "templates_dir": "test_game",
            "tasks": [
                {"name": "sign_in", "type": "tap_template", "template": "sign.png"},
                {"name": "mail_collect", "type": "claim_chain", "templates": ["mail.png", "claim.png"]},
                {"name": "gacha_free", "type": "gacha", "params": {"mode": "free", "template": "gacha.png"}},
            ],
            "risk_control": {"templates": []},
            "safety": {"default_dry_run": True, "max_total_seconds": 300},
        }
    )


def test_game_workflow_dry_run_runs_l1_tasks_without_tapping(tmp_path) -> None:
    driver = _FakeDriver()
    notifier = _RecordingNotifier()
    events: list[tuple[str, str]] = []
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(),
        notifier=notifier,
        templates_root=tmp_path,
        emit_stage_event=lambda stage, status, payload: events.append((stage, status)),
    )

    result = asyncio.run(orchestrator.run(game=_game(), driver=driver, dry_run=True))

    assert result.status == "success"
    assert len(result.tasks) == 3
    assert all(task.status == "dry_run" for task in result.tasks)
    assert driver.taps == []
    assert notifier.sent
    assert ("workflow", "completed") in events


def test_game_workflow_unknown_screen_does_not_block_following_tasks(tmp_path) -> None:
    driver = _FakeDriver(missing_templates={"sign.png"})
    notifier = _RecordingNotifier()
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(),
        notifier=notifier,
        templates_root=tmp_path,
    )

    result = asyncio.run(orchestrator.run(game=_game(), driver=driver, dry_run=False))

    assert result.status == "partial"
    assert result.tasks[0].status == "unknown_screen"
    assert result.tasks[1].succeeded is True
    assert driver.taps, "claim_chain should still execute after unknown sign_in screen"


def test_game_workflow_persists_unknown_screen_screenshot(tmp_path) -> None:
    driver = _FakeDriver(missing_templates={"sign.png"})
    notifier = _RecordingNotifier()
    screenshot_root = tmp_path / "screenshots"
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(),
        notifier=notifier,
        templates_root=tmp_path,
        screenshot_root=screenshot_root,
    )

    result = asyncio.run(orchestrator.run(game=_game(), driver=driver, dry_run=False))

    screenshot_ref = result.tasks[0].screenshot_after_ref
    assert result.tasks[0].status == "unknown_screen"
    assert screenshot_ref
    assert Path(screenshot_ref).is_file()


def test_game_workflow_real_gacha_taps_template_coordinates(tmp_path) -> None:
    driver = _FakeDriver()
    notifier = _RecordingNotifier()
    game = GameConfig.model_validate(
        {
            "id": "test_game",
            "name": "测试游戏",
            "package_candidates": ["com.test.game"],
            "driver": "adb_airtest",
            "templates_dir": "test_game",
            "tasks": [
                {
                    "name": "gacha_half",
                    "type": "gacha",
                    "params": {"mode": "half_price", "daily_max_pulls": 1, "template": "gacha.png"},
                }
            ],
            "risk_control": {"templates": []},
            "safety": {"default_dry_run": False, "max_total_seconds": 300},
        }
    )
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(),
        notifier=notifier,
        templates_root=tmp_path,
    )

    result = asyncio.run(orchestrator.run(game=game, driver=driver, dry_run=False))

    assert result.status == "success"
    assert driver.taps == [(11, 22)]


def test_game_workflow_blocks_real_run_when_risk_templates_unverified(tmp_path) -> None:
    driver = _FakeDriver(missing_templates={"slider.png"})
    notifier = _RecordingNotifier()
    game = GameConfig.model_validate(
        {
            "id": "test_game",
            "name": "测试游戏",
            "package_candidates": ["com.test.game"],
            "driver": "adb_airtest",
            "templates_dir": "test_game",
            "tasks": [{"name": "sign_in", "type": "tap_template", "template": "sign.png"}],
            "risk_control": {"templates": ["slider.png"]},
            "safety": {"default_dry_run": False, "max_total_seconds": 300},
        }
    )
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(),
        notifier=notifier,
        templates_root=tmp_path,
    )

    result = asyncio.run(orchestrator.run(game=game, driver=driver, dry_run=False))

    assert result.status == "not_ready"
    assert result.tasks[0].error == "risk_control_unverified"
    assert driver.taps == []


def test_game_workflow_vision_fallback_uses_allowlisted_action(tmp_path) -> None:
    driver = _FakeDriver(missing_templates={"sign.png"})
    notifier = _RecordingNotifier()
    game = GameConfig.model_validate(
        {
            "id": "test_game",
            "name": "测试游戏",
            "package_candidates": ["com.test.game"],
            "driver": "adb_airtest",
            "templates_dir": "test_game",
            "tasks": [
                {
                    "name": "sign_in",
                    "type": "tap_template",
                    "template": "sign.png",
                    "params": {
                        "vision_actions": [
                            {"action_id": "dismiss_popup", "x": 9, "y": 10}
                        ]
                    },
                }
            ],
            "risk_control": {"templates": []},
            "safety": {"default_dry_run": False, "max_total_seconds": 300},
        }
    )
    orchestrator = GameWorkflowOrchestrator(
        store=None,
        llm_router=_FakeLLM(vision_action_id="dismiss_popup"),
        notifier=notifier,
        templates_root=tmp_path,
    )

    result = asyncio.run(orchestrator.run(game=game, driver=driver, dry_run=False))

    assert result.status == "success"
    assert driver.taps == [(9, 10)]


class _FailingArchival:
    def add_fact(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        raise RuntimeError("archival down")


def test_publish_continues_when_archival_promotion_fails() -> None:
    game = _game()
    store_rows: list[Any] = []
    notifier = _RecordingNotifier()

    class _Store:
        def append(self, record: Any) -> str:
            store_rows.append(record)
            return record.id

    result = GameRunResult(
        game_id=game.id,
        status="success",
        tasks=[TaskResult(name="gacha_free", task_type="gacha", status="success", succeeded=True)],
        rewards_summary="",
        dry_run=False,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    published = publish_run(
        game=game,
        result=result,
        store=_Store(),
        notifier=notifier,
        llm_router=_FakeLLM(),
        assessments={"gacha_free": RewardAssessment(rarity="ssr", items=["SSR"], raw_text="SSR")},
        archival_memory=_FailingArchival(),
    )

    assert published.promoted_to_archival is False
    assert store_rows
    assert notifier.sent
