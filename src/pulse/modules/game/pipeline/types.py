"""Internal data structures for Game workflow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Screenshot:
    image_bytes: bytes
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ref: str = ""


@dataclass(slots=True)
class TaskResult:
    name: str
    task_type: str
    status: str
    succeeded: bool = False
    reward_text: str = ""
    error: str = ""
    error_message: str = ""
    screenshot_after_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.task_type,
            "status": self.status,
            "succeeded": self.succeeded,
            "reward_text": self.reward_text,
            "error": self.error,
            "error_message": self.error_message,
            "screenshot_after_ref": self.screenshot_after_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class RewardAssessment:
    rarity: str = "common"
    items: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rarity": self.rarity,
            "items": list(self.items),
            "raw_text": self.raw_text,
        }


@dataclass(slots=True)
class GameRunResult:
    game_id: str
    status: str
    tasks: list[TaskResult]
    rewards_summary: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime
    promoted_to_archival: bool = False
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.status in {"success", "partial"},
            "run_id": self.run_id,
            "game_id": self.game_id,
            "status": self.status,
            "tasks": [task.to_dict() for task in self.tasks],
            "rewards_summary": self.rewards_summary,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "promoted_to_archival": self.promoted_to_archival,
        }
