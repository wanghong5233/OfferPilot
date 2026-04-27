"""Game-domain runtime configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GameSettings(BaseSettings):
    """Game-domain knobs loaded from ``PULSE_GAME_*`` env vars."""

    adb_serial: str = Field(
        default="",
        description=(
            "Optional adb serial, e.g. 127.0.0.1:16384 for MuMu 12. "
            "When empty, the driver fails loud if more than one device exists."
        ),
    )
    templates_dir: str = Field(
        default="",
        description="Optional override for the game templates root directory.",
    )
    screenshot_dir: str = Field(
        default="",
        description="Optional override for runtime screenshots; defaults to ~/.pulse/game/screenshots.",
    )
    action_jitter_min_ms: int = Field(default=200, ge=0, le=5000)
    action_jitter_max_ms: int = Field(default=800, ge=0, le=10000)
    command_timeout_sec: float = Field(default=8.0, ge=1.0, le=60.0)

    model_config = SettingsConfigDict(
        env_prefix="PULSE_GAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("adb_serial", "templates_dir", "screenshot_dir")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("action_jitter_max_ms")
    @classmethod
    def _validate_jitter(cls, value: int, info) -> int:
        min_value = int(info.data.get("action_jitter_min_ms") or 0)
        if value < min_value:
            raise ValueError("action_jitter_max_ms must be >= action_jitter_min_ms")
        return value


@lru_cache(maxsize=1)
def get_game_settings() -> GameSettings:
    return GameSettings()
