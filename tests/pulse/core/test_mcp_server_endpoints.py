from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")


def test_streamable_http_mcp_endpoint_initialize_list_and_call() -> None:
    app = create_app()
    with TestClient(app) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )
        session_id = str(init_resp.headers.get("Mcp-Session-Id") or "")

        initialized_resp = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        tools_resp = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        call_resp = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "alarm.create",
                    "arguments": {"minutes": 5, "message": "test"},
                },
            },
        )

    assert init_resp.status_code == 200
    assert session_id.startswith("mcp_")
    init_data = init_resp.json()
    assert init_data["result"]["protocolVersion"] == "2025-03-26"
    assert init_data["result"]["serverInfo"]["name"] == "pulse"

    assert initialized_resp.status_code == 202

    assert tools_resp.status_code == 200
    tool_names = {item["name"] for item in tools_resp.json()["result"]["tools"]}
    assert "alarm.create" in tool_names
    assert "module.hello" in tool_names

    assert call_resp.status_code == 200
    call_data = call_resp.json()
    content = call_data["result"]["content"]
    assert isinstance(content, list) and content
    payload = json.loads(content[0]["text"])
    assert payload["ok"] is True
    assert payload["minutes"] == 5
    assert payload["message"] == "test"


def test_streamable_http_mcp_endpoint_rejects_unknown_session() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": "missing"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert resp.status_code == 404
