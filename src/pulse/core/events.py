from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from collections import defaultdict, deque
from queue import Empty, Full, Queue
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class _EventSubscriber:
    queue: Queue[dict[str, Any]]
    event_type: str
    trace_id: str

    def matches(self, row: dict[str, Any]) -> bool:
        if self.event_type and str(row.get("event_type") or "").strip().lower() != self.event_type:
            return False
        if self.trace_id and str(row.get("trace_id") or "").strip() != self.trace_id:
            return False
        return True


@dataclass(slots=True)
class EventSubscription:
    _store: "InMemoryEventStore"
    _subscription_id: str
    _queue: Queue[dict[str, Any]]

    def poll(self, *, timeout_sec: float = 0.0) -> dict[str, Any] | None:
        safe_timeout = max(0.0, float(timeout_sec))
        try:
            return self._queue.get(timeout=safe_timeout)
        except Empty:
            return None

    def close(self) -> None:
        self._store.unsubscribe(self._subscription_id)


class EventBus:
    """In-process pub/sub bus for module-to-module events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._all_handlers: list[EventHandler] = []
        self._lock = RLock()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        with self._lock:
            self._all_handlers.append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        with self._lock:
            if handler in self._all_handlers:
                self._all_handlers.remove(handler)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
            handlers.extend(self._all_handlers)
        event_payload = payload or {}
        for handler in handlers:
            try:
                handler(event_type, event_payload)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("event handler failed: event_type=%s", event_type)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 120:
                result["..."] = "truncated"
                break
            result[str(key)[:120]] = _json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        result: list[Any] = []
        for idx, item in enumerate(value):
            if idx >= 120:
                result.append("truncated")
                break
            result.append(_json_safe(item, depth=depth + 1))
        return result
    return str(value)[:1000]


class InMemoryEventStore:
    """Bounded in-memory event timeline for observability endpoints."""

    def __init__(self, *, max_events: int = 2000) -> None:
        safe_max = max(100, min(int(max_events), 20000))
        self._max_events = safe_max
        self._events: deque[dict[str, Any]] = deque(maxlen=safe_max)
        self._subscribers: dict[str, _EventSubscriber] = {}
        self._lock = RLock()

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        safe_payload = _json_safe(dict(payload or {}))
        trace_id = str(safe_payload.get("trace_id") or "").strip() or None
        row = {
            "event_id": f"evt_{uuid4().hex[:12]}",
            "timestamp": now.isoformat(),
            "timestamp_unix": now.timestamp(),
            "event_type": str(event_type or "").strip() or "unknown",
            "trace_id": trace_id,
            "payload": safe_payload,
        }
        with self._lock:
            self._events.append(row)
            subscribers = list(self._subscribers.values())
        for subscriber in subscribers:
            if not subscriber.matches(row):
                continue
            try:
                subscriber.queue.put_nowait(dict(row))
            except Full:
                try:
                    subscriber.queue.get_nowait()
                except Empty:
                    pass
                try:
                    subscriber.queue.put_nowait(dict(row))
                except Full:
                    continue

    def recent(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 2000))
        type_filter = str(event_type or "").strip().lower()
        trace_filter = str(trace_id or "").strip()
        rows: list[dict[str, Any]] = []
        with self._lock:
            for item in reversed(self._events):
                if type_filter and str(item.get("event_type") or "").strip().lower() != type_filter:
                    continue
                if trace_filter and str(item.get("trace_id") or "").strip() != trace_filter:
                    continue
                rows.append(dict(item))
                if len(rows) >= safe_limit:
                    break
        return rows

    def export(
        self,
        *,
        limit: int = 1000,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.recent(limit=limit, event_type=event_type, trace_id=trace_id)
        rows.reverse()
        return rows

    def subscribe(
        self,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
        buffer_size: int = 200,
    ) -> EventSubscription:
        safe_buffer = max(10, min(int(buffer_size), 2000))
        subscription_id = f"sub_{uuid4().hex[:12]}"
        subscriber = _EventSubscriber(
            queue=Queue(maxsize=safe_buffer),
            event_type=str(event_type or "").strip().lower(),
            trace_id=str(trace_id or "").strip(),
        )
        with self._lock:
            self._subscribers[subscription_id] = subscriber
        return EventSubscription(
            _store=self,
            _subscription_id=subscription_id,
            _queue=subscriber.queue,
        )

    def unsubscribe(self, subscription_id: str) -> None:
        safe_id = str(subscription_id or "").strip()
        if not safe_id:
            return
        with self._lock:
            self._subscribers.pop(safe_id, None)

    def retention(self) -> dict[str, Any]:
        with self._lock:
            subscribers_total = len(self._subscribers)
        return {
            "mode": "memory_with_export",
            "max_events": self._max_events,
            "export_supported": True,
            "replay_supported": True,
            "stream_supported": True,
            "subscribers": subscribers_total,
        }

    def stats(self, *, window_minutes: int = 60) -> dict[str, Any]:
        safe_window = max(1, min(int(window_minutes), 24 * 60))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=safe_window)
        total = 0
        in_window = 0
        by_type: dict[str, int] = {}
        with self._lock:
            for item in self._events:
                total += 1
                event_type = str(item.get("event_type") or "unknown")
                by_type[event_type] = by_type.get(event_type, 0) + 1
                ts_raw = item.get("timestamp_unix")
                try:
                    ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                except Exception:
                    continue
                if ts >= cutoff:
                    in_window += 1
        top_types = sorted(by_type.items(), key=lambda pair: pair[1], reverse=True)[:20]
        return {
            "total": total,
            "window_minutes": safe_window,
            "in_window": in_window,
            "top_event_types": [{"event_type": key, "count": value} for key, value in top_types],
            "retention": self.retention(),
        }

    def clear(self) -> int:
        with self._lock:
            removed = len(self._events)
            self._events.clear()
        return removed
