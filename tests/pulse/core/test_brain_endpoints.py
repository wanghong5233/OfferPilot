from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")


def test_brain_and_mcp_endpoints() -> None:
    app = create_app()
    with TestClient(app) as client:
        tools_resp = client.get("/api/brain/tools")
        memory_update_resp = client.post(
            "/api/memory/core/update",
            json={"block": "prefs", "content": {"default_location": "hangzhou"}},
        )
        run_resp = client.post(
            "/api/brain/run",
            json={"query": '/tool weather.current {"location":"hangzhou"}', "prefer_llm": False, "max_steps": 6},
        )
        memory_recent_resp = client.get("/api/memory/recall/recent", params={"limit": 10})
        memory_search_resp = client.post("/api/memory/search", json={"query": "hangzhou", "top_k": 5})
        mcp_tools_resp = client.get("/api/mcp/tools")
        mcp_call_resp = client.post(
            "/api/mcp/call",
            json={"name": "weather.current", "arguments": {"location": "Shanghai"}},
        )
        events_recent_resp = client.get("/api/system/events/recent", params={"limit": 50})
        events_stats_resp = client.get("/api/system/events/stats", params={"window_minutes": 60})

    assert tools_resp.status_code == 200
    tools_data = tools_resp.json()
    names = {item["name"] for item in tools_data["items"]}
    assert "weather.current" in names
    assert "flight.search" in names
    assert "alarm.create" in names
    assert "memory_read" in names
    assert "memory_update" in names
    assert "memory_search" in names
    assert "module.hello" in names

    assert memory_update_resp.status_code == 200
    assert memory_update_resp.json()["ok"] is True

    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["ok"] is True
    assert str(run_data["trace_id"]).startswith("trace_")
    assert int(run_data["latency_ms"]) >= 0
    assert "weather.current" in run_data["result"]["used_tools"]

    assert memory_recent_resp.status_code == 200
    assert memory_recent_resp.json()["total"] >= 2

    assert memory_search_resp.status_code == 200
    assert memory_search_resp.json()["total"] >= 1

    assert mcp_tools_resp.status_code == 200
    mcp_tools_data = mcp_tools_resp.json()
    assert mcp_tools_data["local_total"] >= 1
    assert "external_enabled" in mcp_tools_data

    assert mcp_call_resp.status_code == 200
    mcp_call_data = mcp_call_resp.json()
    assert mcp_call_data["ok"] is True
    assert mcp_call_data["name"] == "weather.current"
    assert str(mcp_call_data["trace_id"]).startswith("trace_")
    assert int(mcp_call_data["latency_ms"]) >= 0

    assert events_recent_resp.status_code == 200
    events_recent_data = events_recent_resp.json()
    assert events_recent_data["total"] >= 1
    event_types = {str(item.get("event_type") or "") for item in events_recent_data["items"]}
    assert "brain.run.completed" in event_types
    assert "mcp.call.completed" in event_types

    assert events_stats_resp.status_code == 200
    events_stats_data = events_stats_resp.json()
    assert events_stats_data["ok"] is True
    assert int(events_stats_data["result"]["total"]) >= 1
