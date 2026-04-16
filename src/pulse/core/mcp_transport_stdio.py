from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from typing import Any

from .mcp_client import MCPTool

logger = logging.getLogger(__name__)


class StdioMCPTransport:
    """Standard MCP transport over subprocess stdio (JSON-RPC 2.0).

    Launches an external MCP server as a child process and communicates
    via newline-delimited JSON-RPC on stdin/stdout.
    """

    def __init__(
        self,
        *,
        server_name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: float = 15.0,
    ) -> None:
        self._server_name = str(server_name or "stdio").strip()
        self._command = str(command or "").strip()
        if not self._command:
            raise ValueError("command is required for stdio transport")
        self._args = list(args or [])
        self._env = dict(env or {})
        self._timeout_sec = max(3.0, min(float(timeout_sec), 60.0))
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_process(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process

        self._initialized = False

        merged_env = dict(os.environ)
        merged_env.update(self._env)
        try:
            self._process = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"MCP server command not found: {self._command}. "
                f"Ensure it is installed and in PATH."
            ) from exc

        if not self._initialized:
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pulse", "version": "1.0"},
            })
            self._send_notification("notifications/initialized")
            self._initialized = True

        return self._process

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        proc = self._ensure_process()
        assert proc.stdin is not None and proc.stdout is not None

        self._request_id += 1
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._request_id,
        }
        if params is not None:
            msg["params"] = params

        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._lock:
            proc.stdin.write(payload.encode("utf-8"))
            proc.stdin.flush()

            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server '{self._server_name}' returned empty response")

        try:
            response = json.loads(line.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from MCP server: {line[:200]}") from exc

        if "error" in response:
            err = response["error"]
            code = err.get("code", -1) if isinstance(err, dict) else -1
            message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"MCP JSON-RPC error {code}: {message}")

        return dict(response.get("result") or {})

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        proc = self._ensure_process()
        assert proc.stdin is not None

        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params

        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._lock:
            proc.stdin.write(payload.encode("utf-8"))
            proc.stdin.flush()

    def list_tools(self) -> list[MCPTool]:
        try:
            result = self._send_request("tools/list")
        except Exception as exc:
            logger.warning("Failed to list tools from '%s': %s", self._server_name, exc)
            return []

        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            return []

        tools: list[MCPTool] = []
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            tools.append(MCPTool(
                server=self._server_name,
                name=name,
                description=str(item.get("description") or name),
                schema=dict(item.get("inputSchema") or item.get("schema") or {}),
            ))
        return tools

    def call_tool(self, server: str, name: str, arguments: dict[str, Any]) -> Any:
        result = self._send_request("tools/call", {
            "name": str(name or "").strip(),
            "arguments": dict(arguments or {}),
        })
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                str(c.get("text", "")) for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            return "\n".join(texts) if texts else result
        return result

    def close(self) -> None:
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close() if proc.stdin else None
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._process = None
        self._initialized = False
