from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")

def test_boss_greet_scan_and_trigger_routes(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_PROVIDER", "web_search")
    monkeypatch.setenv("PULSE_BOSS_ALLOW_SEED_FALLBACK", "true")
    app = create_app()
    with TestClient(app) as client:
        health_resp = client.get("/api/modules/boss_greet/health")
        session_resp = client.get("/api/modules/boss_greet/session/check")
        scan_resp = client.post(
            "/api/modules/boss_greet/scan",
            json={"keyword": "AI", "max_items": 5, "max_pages": 2},
        )
        trigger_resp = client.post(
            "/api/modules/boss_greet/trigger",
            json={
                "keyword": "AI Agent",
                "batch_size": 3,
                "match_threshold": 60,
                "greeting_text": "你好",
                "job_type": "intern",
                "run_id": "run-1",
                "confirm_execute": False,
            },
        )

    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert health_resp.json()["runtime"]["mode"] in {"real_connector", "degraded_connector"}
    assert health_resp.json()["runtime"]["provider"] == "boss_web_search"
    assert "provider" in health_resp.json()["runtime"]
    assert session_resp.status_code == 200
    assert "status" in session_resp.json()

    assert scan_resp.status_code == 200
    scan_data = scan_resp.json()
    assert scan_data["keyword"] == "AI"
    assert scan_data["total"] == len(scan_data["items"])
    assert scan_data["total"] <= 5

    assert trigger_resp.status_code == 200
    trigger_data = trigger_resp.json()
    assert trigger_data["ok"] is True
    assert isinstance(trigger_data["matched_details"], list)
    assert trigger_data["greeted"] <= 3
    if trigger_data.get("needs_confirmation"):
        with TestClient(app) as client:
            confirm_resp = client.post(
                "/api/modules/boss_greet/trigger",
                json={
                    "keyword": "AI Agent",
                    "batch_size": 3,
                    "match_threshold": 60,
                    "greeting_text": "你好",
                    "job_type": "intern",
                    "run_id": "run-1-confirm",
                    "confirm_execute": True,
                },
            )
        assert confirm_resp.status_code == 200
        confirm_data = confirm_resp.json()
        assert confirm_data["ok"] is True
        assert confirm_data.get("needs_confirmation") is False
