from __future__ import annotations

from pulse.integrations.boss.connector import BossPlatformConnector


def test_boss_connector_prefers_mcp_when_both_configured(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_BASE_URL", "http://127.0.0.1:8811")
    monkeypatch.setenv("PULSE_BOSS_OPENAPI_BASE_URL", "http://127.0.0.1:8899")
    monkeypatch.delenv("PULSE_BOSS_PROVIDER", raising=False)

    connector = BossPlatformConnector()
    assert connector.provider_name == "boss_mcp"
    assert connector.execution_ready is True


def test_boss_connector_honors_explicit_openapi(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_BASE_URL", "http://127.0.0.1:8811")
    monkeypatch.setenv("PULSE_BOSS_OPENAPI_BASE_URL", "http://127.0.0.1:8899")
    monkeypatch.setenv("PULSE_BOSS_PROVIDER", "openapi")

    connector = BossPlatformConnector()
    assert connector.provider_name == "boss_openapi"
    assert connector.execution_ready is True


def test_boss_connector_stays_unconfigured_without_real_connector(monkeypatch) -> None:
    monkeypatch.delenv("PULSE_BOSS_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("PULSE_BOSS_OPENAPI_BASE_URL", raising=False)
    monkeypatch.delenv("PULSE_BOSS_PROVIDER", raising=False)
    monkeypatch.setenv("PULSE_BOSS_ALLOW_SEED_FALLBACK", "false")

    connector = BossPlatformConnector()
    assert connector.provider_name == "boss_unconfigured"
    assert connector.execution_ready is False
    login = connector.check_login()
    assert login["ok"] is False
    assert login["status"] == "provider_unavailable"


def test_boss_connector_allows_explicit_web_search_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("PULSE_BOSS_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("PULSE_BOSS_OPENAPI_BASE_URL", raising=False)
    monkeypatch.setenv("PULSE_BOSS_PROVIDER", "web_search")
    monkeypatch.setenv("PULSE_BOSS_ALLOW_SEED_FALLBACK", "true")

    connector = BossPlatformConnector()
    assert connector.provider_name == "boss_web_search"
    assert connector.execution_ready is False
