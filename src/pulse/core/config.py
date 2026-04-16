from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="Pulse")
    environment: str = Field(default="dev")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8010, ge=1, le=65535)
    reload: bool = Field(default=False)
    router_rules_path: str = Field(default="config/router_rules.json")
    policy_rules_path: str = Field(default="config/policy_rules.json")
    policy_blocked_keywords: str = Field(default="")
    policy_confirm_keywords: str = Field(default="")
    feishu_sign_secret: str = Field(default="")
    brain_max_steps: int = Field(default=20, ge=1, le=20)
    brain_daily_budget_usd: float = Field(default=2.0, ge=0.0)
    brain_prefer_llm: bool = Field(default=True)
    core_memory_path: str = Field(default="~/.pulse/core_memory.json")
    recall_collection_name: str = Field(default="pulse_recall_memory")
    archival_collection_name: str = Field(default="pulse_archival_memory")
    governance_audit_path: str = Field(default="~/.pulse/governance_audit.json")
    governance_rules_versions_path: str = Field(default="~/.pulse/governance_rules_versions.json")
    evolution_rules_path: str = Field(default="config/evolution_rules.json")
    evolution_default_mode: str = Field(default="autonomous")
    evolution_prefs_mode: str = Field(default="autonomous")
    evolution_soul_mode: str = Field(default="supervised")
    evolution_belief_mode: str = Field(default="autonomous")
    dpo_pairs_path: str = Field(default="~/.pulse/dpo_pairs.jsonl")
    dpo_auto_collect: bool = Field(default=True)
    soul_config_path: str = Field(default="config/soul.yaml")
    memory_recent_limit: int = Field(default=8, ge=1, le=50)
    generated_skills_dir: str = Field(default="generated/skills")
    mcp_http_base_url: str = Field(default="")
    mcp_http_timeout_sec: float = Field(default=8.0, ge=1.0, le=30.0)
    mcp_http_auth_token: str = Field(default="")
    mcp_servers_config_path: str = Field(default="config/mcp_servers.yaml")
    mcp_preferred_server: str = Field(default="")
    event_store_max_events: int = Field(default=2000, ge=100, le=20000)

    model_config = SettingsConfigDict(
        env_prefix="PULSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
