from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

CARD_COLORS = {
    "info": "blue",
    "warning": "orange",
    "critical": "red",
}


def resolve_webhook_url() -> str:
    return os.getenv("NOTIFY_WEBHOOK_URL", "").strip()


def resolve_mode() -> str:
    return os.getenv("NOTIFY_MODE", "feishu_text").strip().lower()


def resolve_timeout_sec() -> float:
    raw = os.getenv("NOTIFY_TIMEOUT_SEC", "8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 8.0
    return max(2.0, min(value, 30.0))


def _build_feishu_card(
    *,
    title: str,
    content: str,
    level: str = "info",
    fields: list[tuple[str, str]] | None = None,
    footer_text: str = "Pulse",
) -> dict[str, Any]:
    color = CARD_COLORS.get(level, "blue")
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": content,
        },
    ]
    if fields:
        columns = []
        for name, value in fields:
            columns.append(
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "is_short": True,
                            "text": {"tag": "lark_md", "content": f"**{name}**\n{value}"},
                        }
                    ],
                }
            )
        elements.append({"tag": "column_set", "flex_mode": "bisect", "columns": columns})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"{footer_text} · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                }
            ],
        }
    )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }


def build_payload(
    message: str,
    *,
    mode: str | None = None,
    title: str | None = None,
    level: str = "info",
    fields: list[tuple[str, str]] | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "pulse",
    footer_text: str = "Pulse",
) -> dict[str, Any]:
    current_mode = (mode or resolve_mode()).strip().lower()
    if current_mode == "feishu_card":
        return _build_feishu_card(
            title=title or "Pulse 通知",
            content=message,
            level=level,
            fields=fields,
            footer_text=footer_text,
        )
    if current_mode == "feishu_text":
        prefix = {"info": "📋", "warning": "⚠️", "critical": "🚨"}.get(level, "📋")
        return {"msg_type": "text", "content": {"text": f"{prefix} {message}"}}
    return {
        "source": source,
        "level": level,
        "message": message,
        "payload": payload or {},
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }


def post_webhook(
    body: dict[str, Any],
    *,
    webhook_url: str | None = None,
    timeout_sec: float | None = None,
) -> tuple[bool, str | None]:
    target_url = (webhook_url or resolve_webhook_url()).strip()
    if not target_url:
        return False, "NOTIFY_WEBHOOK_URL not set"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec or resolve_timeout_sec()) as response:
            if 200 <= response.status < 300:
                return True, None
            return False, f"webhook status={response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http error: {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"url error: {exc.reason}"
    except Exception as exc:  # pragma: no cover
        return False, str(exc)
