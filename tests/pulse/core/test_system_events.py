from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pulse.core.server import create_app

pytestmark = pytest.mark.usefixtures("postgres_test_db")


def _decode_line(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def test_system_events_stream_export_and_stats_endpoints() -> None:
    app = create_app()
    app.state.event_store.record("custom.test", {"trace_id": "trace-custom", "value": 1})

    with TestClient(app) as client:
        stats_resp = client.get("/api/system/events/stats")
        export_resp = client.get("/api/system/events/export", params={"limit": 10, "format": "jsonl"})
        with client.stream(
            "GET",
            "/api/system/events/stream?replay_last=1&event_type=custom.test&trace_id=trace-custom&max_events=1",
        ) as stream_resp:
            lines: list[str] = []
            for raw_line in stream_resp.iter_lines():
                line = _decode_line(raw_line)
                if not line:
                    continue
                lines.append(line)
                if line.startswith("data: "):
                    break

    assert stats_resp.status_code == 200
    stats_data = stats_resp.json()
    assert stats_data["ok"] is True
    retention = stats_data["result"]["retention"]
    assert retention["stream_supported"] is True
    assert retention["export_supported"] is True

    assert export_resp.status_code == 200
    rows = [json.loads(line) for line in export_resp.text.splitlines() if line.strip()]
    assert any(row["event_type"] == "custom.test" for row in rows)

    assert stream_resp.status_code == 200
    assert any(line == "event: custom.test" for line in lines)
    data_line = next(line for line in lines if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["event_type"] == "custom.test"
    assert payload["trace_id"] == "trace-custom"


def test_key_modules_emit_stage_events(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_ALLOW_LOCAL_INBOX_FALLBACK", "true")
    app = create_app()
    modules = {module.name: module for module in app.state.module_registry.modules}
    boss_greet = modules["boss_greet"]
    boss_chat = modules["boss_chat"]

    monkeypatch.setattr(
        boss_greet._connector,
        "scan_jobs",
        lambda **_: {
            "ok": True,
            "items": [
                {
                    "job_id": "job-1",
                    "title": "AI Agent Intern",
                    "company": "Pulse Labs",
                    "salary": "15K-25K",
                    "source_url": "https://www.zhipin.com/job_detail/1",
                    "snippet": "职位描述",
                    "source": "boss_test_scan",
                }
            ],
            "pages_scanned": 1,
            "source": "boss_test_scan",
            "errors": [],
        },
    )

    greet_result = boss_greet.run_scan(keyword="AI Agent", max_items=5, max_pages=1)

    boss_chat._connector._mode = "mcp"
    boss_chat._connector._execution_ready = True
    monkeypatch.setattr(
        boss_chat._connector,
        "pull_conversations",
        lambda **_: {
            "ok": True,
            "items": [
                {
                    "conversation_id": "conv-1",
                    "hr_name": "赵老师",
                    "company": "Pulse Labs",
                    "job_title": "AI Agent Intern",
                    "latest_message": "请补充你的项目经历和到岗时间。",
                    "latest_time": "刚刚",
                    "unread_count": 1,
                }
            ],
            "source": "boss_test_chat",
            "errors": [],
        },
    )
    process_result = boss_chat.run_process(
        max_conversations=10,
        unread_only=True,
        profile_id="default",
        notify_on_escalate=True,
        fetch_latest_hr=True,
        auto_execute=False,
        chat_tab="未读",
        confirm_execute=False,
    )

    with TestClient(app) as client:
        greet_events_resp = client.get(
            "/api/system/events/recent",
            params={"trace_id": greet_result["trace_id"], "limit": 20},
        )
        process_events_resp = client.get(
            "/api/system/events/recent",
            params={"trace_id": process_result["trace_id"], "limit": 50},
        )

    assert greet_events_resp.status_code == 200
    greet_event_types = {item["event_type"] for item in greet_events_resp.json()["items"]}
    assert "module.boss_greet.scan.started" in greet_event_types
    assert "module.boss_greet.scan.completed" in greet_event_types

    assert process_events_resp.status_code == 200
    process_event_types = {item["event_type"] for item in process_events_resp.json()["items"]}
    assert "module.boss_chat.process.started" in process_event_types
    assert "module.boss_chat.inbox_load.started" in process_event_types
    assert "module.boss_chat.inbox_load.completed" in process_event_types
    assert "module.boss_chat.process.completed" in process_event_types
