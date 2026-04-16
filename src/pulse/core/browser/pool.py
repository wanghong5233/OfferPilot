from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable

SessionFactory = Callable[[str], Any]
HealthCheck = Callable[[Any], bool]


@dataclass
class _PooledSession:
    session: Any
    created_at: datetime
    last_used_at: datetime


class BrowserPool:
    """Keyed browser session pool with optional TTL and health check."""

    def __init__(self, *, ttl_seconds: int = 1800, health_check: HealthCheck | None = None) -> None:
        self._ttl = max(1, ttl_seconds)
        self._health_check = health_check
        self._sessions: dict[str, _PooledSession] = {}
        self._lock = RLock()

    def get(self, key: str, *, factory: SessionFactory) -> Any:
        now = datetime.now(timezone.utc)
        with self._lock:
            pooled = self._sessions.get(key)
            if pooled is not None and self._is_reusable(pooled, now=now):
                pooled.last_used_at = now
                return pooled.session

            session = factory(key)
            self._sessions[key] = _PooledSession(
                session=session,
                created_at=now,
                last_used_at=now,
            )
            return session

    def release(self, key: str) -> None:
        with self._lock:
            pooled = self._sessions.pop(key, None)
        if pooled is not None:
            self._close_session(pooled.session)

    def close_all(self) -> None:
        with self._lock:
            values = list(self._sessions.values())
            self._sessions.clear()
        for pooled in values:
            self._close_session(pooled.session)

    def _is_reusable(self, pooled: _PooledSession, *, now: datetime) -> bool:
        if now - pooled.created_at >= timedelta(seconds=self._ttl):
            return False
        if self._health_check is None:
            return True
        return bool(self._health_check(pooled.session))

    @staticmethod
    def _close_session(session: Any) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()
