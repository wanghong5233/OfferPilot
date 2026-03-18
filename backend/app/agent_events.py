"""Agent event bus for real-time observability.

Provides a lightweight in-process event bus that:
- Accepts structured AgentEvent from any backend module
- Fans out to all connected SSE subscribers
- Maintains a bounded history for late-joining clients
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.tz import now_beijing

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    BROWSER_LAUNCH = "browser_launch"
    BROWSER_NAVIGATE = "browser_navigate"
    BROWSER_CLICK = "browser_click"
    BROWSER_INPUT = "browser_input"
    BROWSER_SCREENSHOT = "browser_screenshot"
    BROWSER_CLOSE = "browser_close"
    BROWSER_EXTRACT = "browser_extract"

    LLM_CALL = "llm_call"
    LLM_RESPONSE = "llm_response"

    INTENT_CLASSIFIED = "intent_classified"
    SAFETY_CHECK = "safety_check"
    SAFETY_BLOCKED = "safety_blocked"

    REPLY_GENERATED = "reply_generated"
    REPLY_SENT = "reply_sent"

    ACTION_LOGGED = "action_logged"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_NODE = "workflow_node"
    WORKFLOW_END = "workflow_end"

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AgentEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: now_beijing().strftime("%H:%M:%S"))
    event_type: EventType
    detail: str
    metadata: dict[str, Any] = Field(default_factory=dict)


_MAX_HISTORY = 200
_history: deque[AgentEvent] = deque(maxlen=_MAX_HISTORY)
_subscribers: list[asyncio.Queue[AgentEvent]] = []
_lock = threading.Lock()


def emit(event_type: EventType, detail: str, **metadata: Any) -> AgentEvent:
    """Emit an agent event (thread-safe, callable from sync code)."""
    evt = AgentEvent(event_type=event_type, detail=detail, metadata=metadata)
    with _lock:
        _history.append(evt)
        dead: list[int] = []
        for i, q in enumerate(_subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                dead.append(i)
            except Exception:
                dead.append(i)
        for i in reversed(dead):
            _subscribers.pop(i)
    logger.info("[AGENT] %s | %s", evt.event_type.value, detail)
    return evt


def subscribe() -> asyncio.Queue[AgentEvent]:
    """Create a new subscriber queue. Returns an asyncio.Queue."""
    q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=500)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue[AgentEvent]) -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def get_history(limit: int = 50) -> list[AgentEvent]:
    with _lock:
        items = list(_history)
    return items[-limit:]
