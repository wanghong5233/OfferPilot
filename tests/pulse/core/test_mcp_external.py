from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.core.mcp_client import MCPTool
from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")


class _FakeExternalTransport:
    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                server="ext-demo",
                name="ext.weather",
                description="external weather tool",
                schema={"type": "object"},
            )
        ]

    def call_tool(self, server: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {
            "server": server,
            "name": name,
            "arguments": arguments,
            "ok": True,
        }


def test_mcp_external_transport_endpoints() -> None:
    app = create_app(mcp_transport=_FakeExternalTransport())
    with TestClient(app) as client:
        tools_resp = client.get("/api/mcp/tools")
        preview_resp = client.post(
            "/api/mcp/call",
            json={"server": "ext-demo", "name": "ext.weather", "arguments": {"city": "Hangzhou"}},
        )
        call_resp = client.post(
            "/api/mcp/call",
            json={
                "server": "ext-demo",
                "name": "ext.weather",
                "arguments": {"city": "Hangzhou"},
                "confirm": True,
            },
        )
        brain_tools_resp = client.get("/api/brain/tools")

    assert tools_resp.status_code == 200
    tools_data = tools_resp.json()
    assert tools_data["external_enabled"] is True
    assert tools_data["external_total"] >= 1
    ext_weather = next(item for item in tools_data["external_tools"] if item["name"] == "ext.weather")
    assert "alias" in ext_weather

    assert preview_resp.status_code == 200
    preview_data = preview_resp.json()
    assert preview_data["ok"] is False
    assert preview_data["mode"] == "external"
    assert preview_data["needs_confirmation"] is True

    assert call_resp.status_code == 200
    call_data = call_resp.json()
    assert call_data["ok"] is True
    assert call_data["mode"] == "external"
    assert call_data["result"]["ok"] is True

    assert brain_tools_resp.status_code == 200
    brain_tools = brain_tools_resp.json()["items"]
    alias = ext_weather["alias"]
    ring3_items = {item["name"]: item for item in brain_tools if item["ring"] == "ring3_mcp"}
    assert alias in ring3_items
