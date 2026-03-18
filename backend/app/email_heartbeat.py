from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EmailHeartbeatManager:
    def __init__(
        self,
        *,
        runner: Callable[[int, bool], dict[str, Any]],
        interval_sec: int,
        max_items: int,
        mark_seen: bool,
    ) -> None:
        self._runner = runner
        self._interval_sec = max(30, min(int(interval_sec), 24 * 3600))
        self._max_items = max(1, min(int(max_items), 50))
        self._mark_seen = bool(mark_seen)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._running = False
        self._last_run_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._last_result: dict[str, Any] | None = None

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="email-heartbeat")
            self._running = True
            self._thread.start()
            return True

    def stop(self, *, join_timeout_sec: float = 1.5) -> bool:
        with self._lock:
            was_running = self._running
            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=max(0.1, join_timeout_sec))
        return was_running

    def trigger_once(self) -> dict[str, Any]:
        return self._run_once()

    def status(self) -> dict[str, Any]:
        with self._lock:
            last_result = self._last_result or {}
            return {
                "running": self._running,
                "interval_sec": self._interval_sec,
                "max_items": self._max_items,
                "mark_seen": self._mark_seen,
                "last_run_at": self._last_run_at,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
                "last_fetched_count": (
                    int(last_result.get("fetched_count"))
                    if str(last_result.get("fetched_count", "")).isdigit()
                    else None
                ),
                "last_processed_count": (
                    int(last_result.get("processed_count"))
                    if str(last_result.get("processed_count", "")).isdigit()
                    else None
                ),
            }

    def _loop(self) -> None:
        # Run once on start so scheduler has immediate observable effect.
        try:
            self._run_once()
        except Exception:
            # Error is captured in _run_once status fields.
            pass

        while not self._stop_event.wait(self._interval_sec):
            try:
                self._run_once()
            except Exception:
                # Keep heartbeat resilient.
                continue

    def _run_once(self) -> dict[str, Any]:
        run_at = datetime.utcnow()
        with self._lock:
            self._last_run_at = run_at
        try:
            result = self._runner(self._max_items, self._mark_seen)
            with self._lock:
                self._last_success_at = datetime.utcnow()
                self._last_error = None
                self._last_result = result
            return result
        except Exception as exc:
            err = str(exc)
            logger.warning("Email heartbeat run failed: %s", err)
            with self._lock:
                self._last_error = err[:1000]
            raise
