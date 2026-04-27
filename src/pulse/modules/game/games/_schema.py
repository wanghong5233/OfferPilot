"""GameConfig — YAML contract for one automated game."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

TaskType = Literal["tap_template", "claim_chain", "gacha", "swipe", "wait"]


class ScheduleConfig(BaseModel):
    enabled_by_default: bool = False
    peak_interval_seconds: int = Field(default=24 * 3600, ge=60, le=7 * 24 * 3600)
    offpeak_interval_seconds: int = Field(default=24 * 3600, ge=60, le=7 * 24 * 3600)
    active_hours_only: bool = True
    weekday_windows: list[tuple[int, int]] = Field(default_factory=lambda: [(9, 24)])
    weekend_windows: list[tuple[int, int]] = Field(default_factory=lambda: [(9, 24)])

    @field_validator("weekday_windows", "weekend_windows")
    @classmethod
    def _validate_windows(cls, value: list[tuple[int, int]]) -> list[tuple[int, int]]:
        for start, end in value:
            if not 0 <= int(start) <= 24 or not 0 <= int(end) <= 24 or int(start) >= int(end):
                raise ValueError("time windows must be integer half-open hours within 0..24")
        return [(int(start), int(end)) for start, end in value]


class RiskControlConfig(BaseModel):
    templates: list[str] = Field(default_factory=list)


class SafetyConfig(BaseModel):
    default_dry_run: bool = True
    jitter_ms: tuple[int, int] = Field(default=(200, 800))
    max_total_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("jitter_ms")
    @classmethod
    def _validate_jitter(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = int(value[0]), int(value[1])
        if low < 0 or high < low:
            raise ValueError("jitter_ms must be [min, max] with 0 <= min <= max")
        return (low, high)


class PublishConfig(BaseModel):
    notifier_channels: list[str] = Field(default_factory=lambda: ["console", "feishu"])
    promote_archival_on: list[str] = Field(default_factory=lambda: ["ssr", "limited", "special_event"])


class GameTaskConfig(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    type: TaskType
    template: str = ""
    templates: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_task_shape(self) -> "GameTaskConfig":
        if self.type == "tap_template" and not self.template.strip():
            raise ValueError("tap_template task requires template")
        if self.type == "claim_chain" and not self.templates:
            raise ValueError("claim_chain task requires templates")
        if self.type == "gacha":
            mode = str(self.params.get("mode") or "").strip()
            if mode not in {"free", "half_price"}:
                raise ValueError("gacha task params.mode must be free or half_price")
        return self


class GameConfig(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=120)
    package_candidates: list[str] = Field(default_factory=list)
    main_activity: str = ""
    driver: str = Field(default="adb_airtest", min_length=1)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    templates_dir: str = Field(..., min_length=1)
    tasks: list[GameTaskConfig] = Field(default_factory=list)
    risk_control: RiskControlConfig = Field(default_factory=RiskControlConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)

    @field_validator("package_candidates")
    @classmethod
    def _validate_packages(cls, value: list[str]) -> list[str]:
        cleaned = [str(item or "").strip() for item in value if str(item or "").strip()]
        if not cleaned:
            raise ValueError("game must declare at least one package candidate")
        return cleaned

    @field_validator("tasks")
    @classmethod
    def _at_least_one_task(cls, value: list[GameTaskConfig]) -> list[GameTaskConfig]:
        if not value:
            raise ValueError("game must declare at least one task")
        return value

    @property
    def patrol_name(self) -> str:
        return f"game.workflow.{self.id}"


def load_game_file(path: Path) -> GameConfig:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"failed to read game file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"game file {path} must be a YAML mapping, got {type(data).__name__}")
    if "id" not in data:
        data["id"] = path.stem
    try:
        return GameConfig.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"invalid game config {path}: {exc}") from exc


def discover_game_files(games_dir: Path) -> list[Path]:
    if not games_dir.is_dir():
        return []
    return sorted(
        path
        for path in games_dir.glob("*.yaml")
        if path.is_file() and not path.name.startswith("_")
    )


def load_game_configs(games_dir: Path) -> list[GameConfig]:
    configs: list[GameConfig] = []
    for path in discover_game_files(games_dir):
        config = load_game_file(path)
        logger.info("Loaded game config: id=%s tasks=%d", config.id, len(config.tasks))
        configs.append(config)
    return configs
