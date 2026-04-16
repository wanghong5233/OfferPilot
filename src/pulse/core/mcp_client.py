from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPTool:
    server: str
    name: str
    description: str
    schema: dict[str, Any]


class MCPTransport(Protocol):
    def list_tools(self) -> list[MCPTool]:
        ...

    def call_tool(self, server: str, name: str, arguments: dict[str, Any]) -> Any:
        ...


class MCPClient:
    """MCP client supporting multiple transports (http, stdio, etc.)."""

    def __init__(
        self,
        *,
        transport: MCPTransport | None = None,
        transports: dict[str, MCPTransport] | None = None,
    ) -> None:
        self._transports: dict[str, MCPTransport] = {}
        if transports:
            self._transports.update(transports)
        if transport is not None:
            self._transports["_default"] = transport

    def add_transport(self, name: str, transport: MCPTransport) -> None:
        self._transports[str(name or "_default").strip()] = transport

    def list_tools(self) -> list[MCPTool]:
        all_tools: list[MCPTool] = []
        for transport_name, transport in self._transports.items():
            try:
                tools = transport.list_tools()
                all_tools.extend(tools or [])
            except Exception as exc:
                logger.warning("Failed to list tools from transport '%s': %s", transport_name, exc)
        return all_tools

    async def call_tool(self, *, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        payload = dict(arguments or {})
        safe_server = str(server or "").strip()

        transport = self._transports.get(safe_server)
        if transport is None:
            transport = self._transports.get("_default")
        if transport is None:
            raise RuntimeError(f"No MCP transport configured for server '{safe_server}'")

        result = transport.call_tool(safe_server, name, payload)
        if inspect.isawaitable(result):
            return await result
        return result
