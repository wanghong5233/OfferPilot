from __future__ import annotations

import asyncio

import pulse.core.mcp_server as mcp_server_module
from pulse.core.mcp_server import MCPServerAdapter
from pulse.core.tool import ToolRegistry, tool


@tool(name="demo.plus", description="plus tool")
def _plus(args: dict[str, object]) -> dict[str, object]:
    a = int(args.get("a") or 0)
    b = int(args.get("b") or 0)
    return {"value": a + b}


def test_mcp_server_lists_and_calls_tools() -> None:
    registry = ToolRegistry()
    registry.register_callable(_plus)
    server = MCPServerAdapter(tool_registry=registry)
    tools = server.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "demo.plus"

    resp = asyncio.run(server.call_tool(name="demo.plus", arguments={"a": 2, "b": 3}))
    assert resp["name"] == "demo.plus"
    assert resp["result"]["value"] == 5


def test_mcp_server_main_uses_stdio_builder(monkeypatch) -> None:
    called: dict[str, bool] = {}

    class _FakeServer:
        def serve_stdio(self) -> None:
            called["served"] = True

    monkeypatch.setattr(mcp_server_module, "build_stdio_server", lambda: _FakeServer())
    assert mcp_server_module.main() == 0
    assert called["served"] is True
