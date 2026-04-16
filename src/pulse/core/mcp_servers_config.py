from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    transport: str
    url: str
    timeout_sec: float
    auth_token: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _safe_timeout(raw: Any, *, default: float) -> float:
    try:
        value = float(raw)
    except Exception:
        value = default
    return max(1.0, min(value, 60.0))


def _read_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_mcp_servers(path_value: str) -> list[MCPServerConfig]:
    if not str(path_value or "").strip():
        return []
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        return []
    try:
        payload = _read_yaml(path)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    raw_servers = payload.get("servers")
    if not isinstance(raw_servers, list):
        return []

    servers: list[MCPServerConfig] = []
    for item in raw_servers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        transport = str(item.get("transport") or "http").strip().lower()
        if transport == "http+sse":
            transport = "http_sse"
        if transport == "streamable-http":
            transport = "streamable_http"
        if not name:
            continue

        auth_token = str(item.get("auth_token") or "").strip()
        auth_token_env = str(item.get("auth_token_env") or "").strip()
        if not auth_token and auth_token_env:
            auth_token = str(os.getenv(auth_token_env, "") or "").strip()
        timeout_sec = _safe_timeout(item.get("timeout_sec"), default=8.0)

        if transport == "stdio":
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            raw_args = item.get("args")
            args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            raw_env = item.get("env")
            env = {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}
            servers.append(MCPServerConfig(
                name=name, transport="stdio", url="", timeout_sec=timeout_sec,
                auth_token=auth_token, command=command, args=args, env=env, raw=dict(item),
            ))
        elif transport in {"http", "streamable_http", "http_sse", "sse", "legacy_sse"}:
            url = str(item.get("url") or item.get("base_url") or "").strip().rstrip("/")
            if not url:
                continue
            servers.append(MCPServerConfig(
                name=name, transport=transport, url=url, timeout_sec=timeout_sec,
                auth_token=auth_token, raw=dict(item),
            ))

    return servers


def pick_preferred_http_server(
    servers: list[MCPServerConfig],
    *,
    preferred_name: str,
) -> MCPServerConfig | None:
    http_servers = [s for s in servers if s.transport in {"http", "streamable_http", "http_sse", "sse", "legacy_sse"}]
    if not http_servers:
        return None
    wanted = str(preferred_name or "").strip().lower()
    if wanted:
        for item in http_servers:
            if item.name.strip().lower() == wanted:
                return item
    return http_servers[0]
