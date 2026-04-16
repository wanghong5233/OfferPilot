from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")


def test_server_exposes_router_policy_and_channel_state() -> None:
    app = create_app()
    assert hasattr(app.state, "intent_router")
    assert hasattr(app.state, "policy_engine")
    assert hasattr(app.state, "channel_adapters")
    assert hasattr(app.state, "tool_registry")
    assert hasattr(app.state, "brain")
    assert hasattr(app.state, "skill_generator")
    assert hasattr(app.state, "archival_memory")
    assert hasattr(app.state, "workspace_memory")
    assert hasattr(app.state, "governance")
    assert hasattr(app.state, "evolution_engine")
    assert hasattr(app.state, "dpo_collector")
    assert hasattr(app.state, "governance_rules_versions")
    assert hasattr(app.state, "mcp_server")
    assert hasattr(app.state, "agent_runtime")
    adapters = app.state.channel_adapters
    assert "cli" in adapters
    assert "feishu" in adapters
    hooks_list = app.state.agent_runtime._hooks.list_hooks()
    assert "beforeTaskStart" in hooks_list
    assert "beforeToolUse" in hooks_list
    assert "beforePromotion" in hooks_list


def test_route_resolve_endpoint_returns_route_and_policy() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/api/system/route/resolve", json={"text": "ping"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"]["method"] in {"exact", "prefix", "llm", "fallback"}
    assert "action" in data["policy"]


def test_runtime_kernel_wiring_and_heartbeat_endpoints() -> None:
    app = create_app()
    runtime = app.state.agent_runtime

    assert "__runtime_heartbeat__" in runtime.runner.engine.list_tasks()
    assert app.state.workspace_memory is not None

    with TestClient(app) as client:
        heartbeat_resp = client.get("/api/runtime/heartbeat")
        wake_resp = client.post("/api/runtime/wake")

    assert heartbeat_resp.status_code == 200
    heartbeat_data = heartbeat_resp.json()
    assert heartbeat_data["ok"] is True
    assert heartbeat_data["result"]["heartbeat_count"] >= 1

    assert wake_resp.status_code == 200
    wake_data = wake_resp.json()
    assert wake_data["ok"] is True
    assert wake_data["result"]["manual_wake"] is True
