from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from .tool import ToolRegistry


DEFAULT_PROTOCOL_VERSION = "2025-03-26"


class MCPServerAdapter:
    """Expose ToolRegistry via MCP-compatible interface.

    Supports both in-process Python API and standard JSON-RPC over stdio
    for external MCP clients (Claude Desktop, Cursor, etc.).
    """

    def __init__(self, *, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry

    @staticmethod
    def _server_info() -> dict[str, str]:
        return {"name": "pulse", "version": "1.0"}

    @classmethod
    def _initialize_result(cls, protocol_version: str = DEFAULT_PROTOCOL_VERSION) -> dict[str, Any]:
        return {
            "protocolVersion": str(protocol_version or DEFAULT_PROTOCOL_VERSION).strip() or DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": cls._server_info(),
        }

    def list_tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec in self._tool_registry.list_tools():
            rows.append({
                "name": spec.name,
                "description": spec.description,
                "inputSchema": dict(spec.schema) or {"type": "object", "properties": {}},
            })
        return rows

    async def call_tool(self, *, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._tool_registry.invoke(name, arguments or {})
        return {"name": name, "result": result}

    def serve_stdio(self) -> None:
        """Run as a standard MCP Server over stdin/stdout (JSON-RPC 2.0).

        This method blocks and reads JSON-RPC requests from stdin,
        dispatches them, and writes responses to stdout.
        Designed to be launched as: python -m pulse.core.mcp_server
        """
        loop = asyncio.new_event_loop()
        try:
            for line in sys.stdin:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                response = loop.run_until_complete(self.handle_jsonrpc(msg))
                if response is not None:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
        finally:
            loop.close()

    async def handle_jsonrpc(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = str(msg.get("method") or "").strip()
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if msg_id is None:
            if method == "notifications/initialized":
                return None
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": self._initialize_result(),
            }

        if method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"ok": True, "serverInfo": self._server_info()},
            }

        if method == "tools/list":
            tools = self.list_tools()
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": tools},
            }

        if method == "tools/call":
            name = str(params.get("name") or "").strip()
            arguments = dict(params.get("arguments") or {})
            try:
                result = await self._tool_registry.invoke(name, arguments)
                text = json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    async def _handle_jsonrpc(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        return await self.handle_jsonrpc(msg)


def build_stdio_server() -> MCPServerAdapter:
    from .server import create_app

    app = create_app()
    return app.state.mcp_server  # type: ignore[return-value]


def main() -> int:
    server = build_stdio_server()
    server.serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
