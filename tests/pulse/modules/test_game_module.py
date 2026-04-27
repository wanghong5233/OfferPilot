from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pulse.core.storage.engine import DatabaseEngine
from pulse.core.runtime import AgentRuntime, RuntimeConfig
from pulse.modules.game._connectors.registry import build_driver
from pulse.modules.game.config import GameSettings
from pulse.modules.game.games import GameConfig
from pulse.modules.game.module import GameModule
from pulse.modules.game.store import GameRunRecord, GameRunStore


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.used_today = 0

    def list_recent(self, *, game_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self.rows if not game_id or row["game_id"] == game_id]
        return rows[:limit]

    def latest(self, *, game_id: str) -> dict[str, Any] | None:
        rows = self.list_recent(game_id=game_id, limit=1)
        return rows[0] if rows else None

    def append(self, record: Any) -> str:
        payload = record.to_payload()
        self.rows.insert(0, payload)
        return str(payload["id"])

    def count_task_today(self, *, game_id: str, task_name: str, account_id: str = "default") -> int:
        _ = game_id, task_name, account_id
        return self.used_today


class _FakeLLM:
    def invoke_text(self, prompt: str, *, route: str = "default") -> str:
        _ = prompt, route
        return "完成测试游戏日常。"

    def invoke_json(self, prompt: str, *, route: str = "default", default: Any = None) -> Any:
        _ = prompt, route
        return default


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message: Any) -> None:
        self.sent.append(message)


class _FakeSuspendedStore:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.created.append(dict(kwargs))
        return SimpleNamespace(task_id=kwargs["task_id"])


class _FakeDriver:
    provider_name = "fake_game"

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
        _ = image_bytes, template_path, threshold
        return {"ok": True, "source": self.provider_name, "found": True, "x": 1, "y": 2, "score": 0.99}

    def tap(self, *, x: int, y: int) -> dict[str, Any]:
        _ = x, y
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> dict[str, Any]:
        _ = x1, y1, x2, y2, duration_ms
        return {"ok": True, "source": self.provider_name, "status": "sent"}

    def text(self, *, value: str) -> dict[str, Any]:
        _ = value
        return {"ok": True, "source": self.provider_name, "status": "sent"}


@pytest.fixture
def game_dir(tmp_path: Path) -> Path:
    (tmp_path / "test_game.yaml").write_text(
        """
id: test_game
name: 测试游戏
package_candidates: [com.test.game]
driver: adb_airtest
schedule:
  enabled_by_default: false
  peak_interval_seconds: 86400
  offpeak_interval_seconds: 86400
  active_hours_only: true
  weekday_windows: [[9, 24]]
  weekend_windows: [[9, 24]]
templates_dir: test_game
tasks:
  - { name: sign_in, type: tap_template, template: sign.png }
risk_control: { templates: [] }
safety: { default_dry_run: true, max_total_seconds: 300 }
publish: { notifier_channels: [console], promote_archival_on: [ssr, limited, special_event] }
""".strip(),
        encoding="utf-8",
    )
    return tmp_path


def test_game_module_exposes_intents(game_dir: Path) -> None:
    module = GameModule(
        games_dir=game_dir,
        store=_FakeStore(),
        llm_router=_FakeLLM(),
        notifier=_FakeNotifier(),
        archival_memory=object(),
    )

    assert {intent.name for intent in module.intents} == {
        "game.workflow.run",
        "game.runs.list",
        "game.runs.latest",
    }


def test_game_module_registers_disabled_patrol(game_dir: Path) -> None:
    module = GameModule(
        games_dir=game_dir,
        store=_FakeStore(),
        llm_router=_FakeLLM(),
        notifier=_FakeNotifier(),
        archival_memory=object(),
    )
    runtime = AgentRuntime(config=RuntimeConfig())

    module.bind_runtime(runtime)
    module.on_startup()

    patrol = next(item for item in runtime.list_patrols() if item["name"] == "game.workflow.test_game")
    assert patrol["enabled"] is False
    assert patrol["peak_interval_seconds"] == 86400
    assert patrol["weekday_windows"] == ((9, 24),)


def test_game_module_run_workflow_uses_yaml_driver(monkeypatch, game_dir: Path) -> None:
    store = _FakeStore()
    notifier = _FakeNotifier()
    module = GameModule(
        games_dir=game_dir,
        store=store,
        llm_router=_FakeLLM(),
        notifier=notifier,
        archival_memory=object(),
    )

    monkeypatch.setattr(
        "pulse.modules.game.module.build_driver",
        lambda driver_name, *, settings, templates_root: _FakeDriver(),
    )

    import asyncio

    result = asyncio.run(module.run_workflow(game_id="test_game", dry_run=True))

    assert result["ok"] is True
    assert result["game_id"] == "test_game"
    assert result["status"] == "success"
    assert store.rows
    assert notifier.sent


def test_game_module_suspends_half_price_gacha_when_budget_exceeded(game_dir: Path) -> None:
    store = _FakeStore()
    store.used_today = 1
    notifier = _FakeNotifier()
    suspended = _FakeSuspendedStore()
    module = GameModule(
        games_dir=game_dir,
        store=store,
        llm_router=_FakeLLM(),
        notifier=notifier,
        archival_memory=object(),
    )
    module.attach_safety_plane(
        suspended_store=suspended,
        workspace_id="ws-test",
        mode="enforce",
    )
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
        }
    )

    decision = module._evaluate_safety(game, game.tasks[0])

    assert decision["ok"] is False
    assert decision["status"] == "suspended"
    assert suspended.created
    assert notifier.sent


def test_cloud_web_driver_stub_is_registry_selectable(tmp_path: Path) -> None:
    driver = build_driver(
        "cloud_web",
        settings=GameSettings(),
        templates_root=tmp_path,
    )

    health = driver.health()

    assert driver.provider_name == "cloud_web"
    assert driver.execution_ready is False
    assert health["ok"] is False
    assert health["status"] == "not_implemented"


@pytest.mark.usefixtures("postgres_test_db")
def test_game_run_store_budget_ignores_dry_run(postgres_test_db) -> None:
    _ = postgres_test_db
    db = DatabaseEngine()
    db.execute("DROP TABLE IF EXISTS game_runs CASCADE")
    store = GameRunStore(db_engine=db)
    store.ensure_schema()

    store.append(
        GameRunRecord(
            game_id="test_game",
            status="success",
            dry_run=True,
            tasks=[
                {
                    "name": "gacha_half",
                    "type": "gacha",
                    "status": "dry_run",
                    "succeeded": True,
                }
            ],
        )
    )
    assert store.count_task_today(game_id="test_game", task_name="gacha_half") == 0

    store.append(
        GameRunRecord(
            game_id="test_game",
            status="success",
            dry_run=False,
            tasks=[
                {
                    "name": "gacha_half",
                    "type": "gacha",
                    "status": "success",
                    "succeeded": True,
                }
            ],
        )
    )
    assert store.count_task_today(game_id="test_game", task_name="gacha_half") == 1
    db.execute("DROP TABLE IF EXISTS game_runs CASCADE")
