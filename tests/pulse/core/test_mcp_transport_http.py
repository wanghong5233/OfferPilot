from __future__ import annotations

import json
from io import BytesIO
import urllib.error

from pulse.core.mcp_transport_http import HttpMCPTransport


class _FakeResponse:
    def __init__(self, body: dict[str, object] | str, *, content_type: str = "application/json", headers: dict[str, str] | None = None) -> None:
        self.status = 200
        if isinstance(body, str):
            self._raw = body.encode("utf-8")
        else:
            self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Type": content_type, **dict(headers or {})}
        self._stream = BytesIO(self._raw)

    def read(self) -> bytes:
        return self._stream.read()

    def readline(self) -> bytes:
        return self._stream.readline()

    def close(self) -> None:
        return None

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201
        return False


def test_http_mcp_transport_custom_gateway_fallback(monkeypatch) -> None:
    responses = [
        _FakeResponse(
            {
                "tools": [
                    {
                        "server": "ext",
                        "name": "ext.echo",
                        "description": "echo",
                        "schema": {"type": "object"},
                    }
                ]
            }
        ),
        _FakeResponse({"result": {"ok": True, "value": 3}}),
    ]

    def _fake_urlopen(request, timeout=8.0):  # noqa: ANN001, ANN202
        _ = timeout
        url = str(getattr(request, "full_url", "") or "")
        body = request.data.decode("utf-8") if request.data else ""
        if url == "http://localhost:9901" and request.method == "POST" and body:
            raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=BytesIO(b""))
        if url == "http://localhost:9901" and request.method == "GET":
            raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=BytesIO(b""))
        return responses.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    transport = HttpMCPTransport(base_url="http://localhost:9901", timeout_sec=6)
    tools = transport.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "ext.echo"

    result = transport.call_tool("ext", "ext.echo", {"x": 1})
    assert result["ok"] is True


def test_http_mcp_transport_streamable_http(monkeypatch) -> None:
    sse_body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\\"ok\\": true, \\"value\\": 9}"}]}}\n\n'
    )
    responses = [
        _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ext", "version": "1.0"},
                },
            },
            headers={"Mcp-Session-Id": "sess-1"},
        ),
        _FakeResponse({}, headers={}),
        _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "server": "ext",
                            "name": "ext.echo",
                            "description": "echo",
                            "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}},
                        }
                    ]
                },
            }
        ),
        _FakeResponse(sse_body, content_type="text/event-stream"),
    ]

    def _fake_urlopen(request, timeout=8.0):  # noqa: ANN001, ANN202
        _ = timeout
        body = request.data.decode("utf-8") if request.data else ""
        if request.method == "POST" and body:
            parsed = json.loads(body)
            if parsed.get("method") == "initialize":
                assert request.headers.get("Accept") == "application/json, text/event-stream"
            if parsed.get("method") in {"tools/list", "tools/call"}:
                assert request.headers.get("Mcp-session-id") == "sess-1"
        return responses.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    transport = HttpMCPTransport(
        base_url="http://localhost:9902/mcp",
        timeout_sec=6,
        transport_mode="streamable_http",
    )
    tools = transport.list_tools()
    assert len(tools) == 1
    assert tools[0].schema["type"] == "object"

    result = transport.call_tool("ext", "ext.echo", {"x": 9})
    assert result["ok"] is True
    assert result["value"] == 9
