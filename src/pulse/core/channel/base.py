from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

ChannelHandler = Callable[["IncomingMessage"], Any]


@dataclass(slots=True)
class IncomingMessage:
    channel: str
    user_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class OutgoingMessage:
    channel: str
    target_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseChannelAdapter(ABC):
    """Unified message ingress contract for external channels."""

    name: str = "channel"

    def __init__(self) -> None:
        self._handler: ChannelHandler | None = None

    def set_handler(self, handler: ChannelHandler) -> None:
        self._handler = handler

    def dispatch(self, message: IncomingMessage) -> Any:
        if self._handler is None:
            return None
        return self._handler(message)

    @abstractmethod
    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        """Parse channel-specific payload into IncomingMessage."""

    def send(self, message: OutgoingMessage) -> None:  # pragma: no cover - optional integration point
        """Optional egress hook for channels that support outbound messages."""
        return None
