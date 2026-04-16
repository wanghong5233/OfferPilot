from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Notification:
    level: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    def send(self, message: Notification) -> None:
        ...


class ConsoleNotifier:
    """Fallback notifier that writes notifications to logger."""

    def send(self, message: Notification) -> None:
        logger.info("[%s] %s - %s", message.level.upper(), message.title, message.content)


class MultiNotifier:
    """Broadcast notifier that fans out to multiple sinks."""

    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        self._notifiers: list[Notifier] = list(notifiers or [])

    def add(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)

    def send(self, message: Notification) -> None:
        errors: list[str] = []
        for notifier in self._notifiers:
            try:
                notifier.send(message)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(str(exc))
        if errors:
            logger.warning("Notifier fan-out failed for %d sink(s): %s", len(errors), " | ".join(errors))


class FeishuNotifier:
    """Send notifications via Feishu/Lark webhook, implementing the Notifier protocol."""

    def send(self, message: Notification) -> None:
        from .webhook import build_payload, post_webhook

        level = str(message.level or "info").strip() or "info"
        title = str(message.title or "Pulse 通知").strip()
        body = build_payload(
            message.content,
            mode="feishu_card",
            title=title,
            level=level,
        )
        ok, err = post_webhook(body)
        if not ok:
            logger.warning("FeishuNotifier failed: %s", err)
