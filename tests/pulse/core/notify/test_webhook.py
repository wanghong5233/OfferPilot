from __future__ import annotations

from pulse.core.notify.webhook import (
    build_payload,
    post_webhook,
    resolve_mode,
    resolve_timeout_sec,
)


def test_resolve_mode_and_timeout(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_MODE", "feishu_card")
    monkeypatch.setenv("NOTIFY_TIMEOUT_SEC", "11")
    assert resolve_mode() == "feishu_card"
    assert resolve_timeout_sec() == 11.0


def test_build_payload_for_text_mode() -> None:
    payload = build_payload("hello", mode="feishu_text", level="warning")
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"].startswith("⚠️")


def test_build_payload_for_card_mode() -> None:
    payload = build_payload(
        "content",
        mode="feishu_card",
        title="alert",
        level="critical",
        fields=[("a", "1")],
        footer_text="Pulse",
    )
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "red"


def test_post_webhook_returns_error_without_url() -> None:
    ok, err = post_webhook({"k": "v"}, webhook_url="")
    assert ok is False
    assert "NOTIFY_WEBHOOK_URL" in (err or "")
