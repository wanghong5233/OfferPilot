"""OfferPilot 通知模块 — 飞书 Webhook 为主渠道。

支持三种消息格式：
- feishu_text: 纯文本（默认，兼容旧逻辑）
- feishu_card: 飞书互动卡片（带颜色标题、分级告警）
- generic:     通用 JSON（可对接企微/钉钉等 Webhook）

通知级别：
- info:     普通摘要（蓝色卡片）
- warning:  需关注（橙色卡片）
- critical: 紧急告警（红色卡片）
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from app.tz import now_beijing

logger = logging.getLogger(__name__)

CARD_COLORS = {
    "info": "blue",
    "warning": "orange",
    "critical": "red",
}


def _notify_webhook_url() -> str:
    return os.getenv("NOTIFY_WEBHOOK_URL", os.getenv("EMAIL_NOTIFY_WEBHOOK_URL", "")).strip()


def _notify_mode() -> str:
    return os.getenv("NOTIFY_MODE", os.getenv("EMAIL_NOTIFY_MODE", "feishu_text")).strip().lower()


def _notify_timeout_sec() -> float:
    raw = os.getenv("NOTIFY_TIMEOUT_SEC", os.getenv("EMAIL_NOTIFY_TIMEOUT_SEC", "8")).strip()
    try:
        value = float(raw)
    except ValueError:
        value = 8.0
    return max(2.0, min(value, 30.0))


def _build_feishu_card(
    title: str,
    content: str,
    level: str = "info",
    fields: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """构建飞书互动卡片消息体。"""
    color = CARD_COLORS.get(level, "blue")
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": content,
        },
    ]
    if fields:
        field_elements = []
        for name, value in fields:
            field_elements.append({"is_short": True, "text": {"tag": "lark_md", "content": f"**{name}**\n{value}"}})
        elements.append({"tag": "column_set", "flex_mode": "bisect", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [f]} for f in field_elements
        ]})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"OfferPilot · {now_beijing().strftime('%Y-%m-%d %H:%M')}"}],
    })
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


def _build_payload(
    message: str,
    *,
    title: str | None = None,
    level: str = "info",
    fields: list[tuple[str, str]] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = _notify_mode()
    if mode == "feishu_card":
        return _build_feishu_card(
            title=title or "OfferPilot 通知",
            content=message,
            level=level,
            fields=fields,
        )
    if mode == "feishu_text":
        prefix = {"info": "📋", "warning": "⚠️", "critical": "🚨"}.get(level, "📋")
        return {
            "msg_type": "text",
            "content": {"text": f"{prefix} {message}"},
        }
    return {
        "source": "offerpilot",
        "level": level,
        "message": message,
        "payload": payload or {},
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
    }


def _post_webhook(body: dict[str, Any]) -> tuple[bool, str | None]:
    webhook_url = _notify_webhook_url()
    if not webhook_url:
        return False, "NOTIFY_WEBHOOK_URL not set"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_notify_timeout_sec()) as response:
            if 200 <= response.status < 300:
                return True, None
            return False, f"webhook status={response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http error: {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"url error: {exc.reason}"
    except Exception as exc:  # pragma: no cover
        return False, str(exc)


def send_channel_notification(
    message: str,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """向飞书/Webhook 发送普通通知（兼容旧调用方式）。"""
    body = _build_payload(message, payload=payload)
    ok, err = _post_webhook(body)
    if not ok:
        logger.warning("Notification failed: %s", err)
    return ok, err


def notify_alert(
    title: str,
    message: str,
    *,
    level: str = "warning",
    fields: list[tuple[str, str]] | None = None,
) -> tuple[bool, str | None]:
    """发送分级告警通知。level: info / warning / critical"""
    body = _build_payload(message, title=title, level=level, fields=fields)
    ok, err = _post_webhook(body)
    if not ok:
        logger.warning("Alert notification failed [%s]: %s", level, err)
    return ok, err


def notify_cookie_expired(service: str = "BOSS 直聘") -> tuple[bool, str | None]:
    """Cookie 过期紧急告警。"""
    return notify_alert(
        title=f"🚨 {service} Cookie 已过期",
        message=(
            f"**{service}** 浏览器 Cookie 已失效，自动化操作无法继续。\n\n"
            "**请尽快处理：**\n"
            "1. 在 WSL 终端执行 `./scripts/boss-login.sh`\n"
            "2. 用手机扫码重新登录\n"
            "3. 登录成功后关闭浏览器窗口"
        ),
        level="critical",
    )


def notify_daily_summary(
    *,
    scan_count: int = 0,
    chat_processed: int = 0,
    auto_replied: int = 0,
    escalated: int = 0,
    emails_fetched: int = 0,
    errors: list[str] | None = None,
) -> tuple[bool, str | None]:
    """发送每日任务摘要。"""
    lines = [
        f"**BOSS 扫描岗位数：** {scan_count}",
        f"**聊天处理会话数：** {chat_processed}",
        f"**自动回复数：** {auto_replied}",
        f"**升级人工处理数：** {escalated}",
        f"**邮件拉取数：** {emails_fetched}",
    ]
    if errors:
        lines.append(f"\n**异常记录（{len(errors)} 条）：**")
        for err in errors[:5]:
            lines.append(f"- {err}")
        if len(errors) > 5:
            lines.append(f"- ...及其他 {len(errors) - 5} 条")

    level = "critical" if errors else ("warning" if escalated > 0 else "info")
    return notify_alert(
        title="📊 OfferPilot 每日摘要",
        message="\n".join(lines),
        level=level,
        fields=[
            ("扫描", str(scan_count)),
            ("聊天", str(chat_processed)),
            ("自动回复", str(auto_replied)),
            ("人工介入", str(escalated)),
        ],
    )
