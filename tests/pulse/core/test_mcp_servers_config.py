from __future__ import annotations

from types import SimpleNamespace

from pulse.core.server import _build_all_mcp_transports
from pulse.core.mcp_servers_config import load_mcp_servers, pick_preferred_http_server


def test_load_mcp_servers_from_yaml(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "mcp_servers.yaml"
    config_path.write_text(
        "\n".join(
            [
                "servers:",
                "  - name: boss",
                "    transport: http",
                "    url: http://127.0.0.1:8811",
                "    timeout_sec: 10",
                "    auth_token_env: TEST_BOSS_TOKEN",
                "  - name: web-search",
                "    transport: streamable_http",
                "    url: http://127.0.0.1:8812",
                "    timeout_sec: 8",
                "  - name: legacy",
                "    transport: http_sse",
                "    url: http://127.0.0.1:8813/sse",
                "    timeout_sec: 12",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_BOSS_TOKEN", "secret-token")

    servers = load_mcp_servers(str(config_path))
    assert len(servers) == 3
    assert servers[0].name == "boss"
    assert servers[0].auth_token == "secret-token"
    assert servers[1].name == "web-search"
    assert servers[1].transport == "streamable_http"
    assert servers[2].transport == "http_sse"


def test_pick_preferred_server_or_first() -> None:
    servers = load_mcp_servers("not-exists.yaml")
    assert servers == []
    assert pick_preferred_http_server(servers, preferred_name="boss") is None


def test_build_all_mcp_transports_supports_http_family_and_stdio(tmp_path) -> None:
    config_path = tmp_path / "mcp_servers.yaml"
    config_path.write_text(
        "\n".join(
            [
                "servers:",
                "  - name: streamable",
                "    transport: streamable_http",
                "    url: http://127.0.0.1:8811/mcp",
                "  - name: legacy",
                "    transport: http_sse",
                "    url: http://127.0.0.1:8812/sse",
                "  - name: localfs",
                "    transport: stdio",
                "    command: python",
                "    args: ['-V']",
            ]
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(mcp_servers_config_path=str(config_path))

    transports = _build_all_mcp_transports(settings)

    assert set(transports) == {"streamable", "legacy", "localfs"}
    assert transports["streamable"].__class__.__name__ == "HttpMCPTransport"
    assert transports["legacy"].__class__.__name__ == "HttpMCPTransport"
    assert transports["localfs"].__class__.__name__ == "StdioMCPTransport"
