"""Durable storage for runtime patrol enable/disable lifecycle decisions.

**Why this exists** (post-mortem 2026-04-28 trace_753fecf70cc5):

``AgentRuntime`` historically kept the per-patrol ``enabled`` flag purely in
process memory. ``register_patrol`` always defaulted to ``enabled=False`` and
the user flipped it on later via ``system.patrol.enable`` over IM. Any of:

* uvicorn dev-reload (watchfiles touches a script),
* manual restart,
* OS-level crash and re-launch,

silently reset every running patrol to OFF. The bot would still answer
"自动投递服务已为你开启" because the in-turn ``enable_patrol`` call had
returned ``True`` — but five minutes later the user's "long-running service"
was off and nothing scheduled would ever fire. That is the worst kind of bug:
the contract said ON, the runtime said OFF, and there was no observable error
in between.

**Contract**:

* ``record(name, enabled, actor)`` — durable upsert. Atomic write via
  tmp + rename so a crash mid-write cannot corrupt the file.
* ``snapshot()`` — read all known states for rehydration during
  ``register_patrol`` startup.
* JSON document on disk (single file). Small N (a dozen patrols max for the
  foreseeable future) so we don't need a rolling jsonl or DB. ADR-005
  observability fields (``updated_at``, ``actor``) are kept so post-mortem
  tools can answer "who turned this on, when".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PatrolEnabledRecord:
    """One persisted lifecycle decision for a single patrol.

    The runtime treats absence-from-store as "no user decision yet — fall back
    to module's register-time default" (which is always ``False`` per
    ADR-004 §6.1.1).
    """

    name: str
    enabled: bool
    updated_at: str  # ISO-8601 UTC
    actor: str       # "im:user-xyz" / "rest:cli" / "test" / ...


class PatrolEnabledStateStore:
    """File-backed key-value store keyed on patrol ``name``.

    Threading: ``record`` and ``snapshot`` are guarded by an internal lock.
    Use one store instance per ``AgentRuntime``; the file path is the
    serialization point across processes (last-writer-wins, fine for a
    single-user self-hosted assistant).
    """

    def __init__(self, *, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = Lock()
        # Lazy-create parent dir so callers don't have to remember.
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------ read

    def snapshot(self) -> dict[str, PatrolEnabledRecord]:
        """Return all recorded patrols. Missing file → empty dict (cold boot).

        File-format errors are loud (warning + return empty); we **do not**
        delete or truncate the suspect file — manual inspection is preferred
        over auto-corruption-recovery.
        """
        with self._lock:
            return self._read_locked()

    def _read_locked(self) -> dict[str, PatrolEnabledRecord]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("patrol_state_store: read failed path=%s err=%s", self._path, exc)
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "patrol_state_store: corrupt JSON path=%s err=%s; "
                "ignoring (manual fix required, file left intact)",
                self._path, exc,
            )
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "patrol_state_store: top-level is %s, expected object; "
                "ignoring (path=%s)",
                type(data).__name__, self._path,
            )
            return {}
        out: dict[str, PatrolEnabledRecord] = {}
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            try:
                out[str(name)] = PatrolEnabledRecord(
                    name=str(name),
                    enabled=bool(entry.get("enabled", False)),
                    updated_at=str(entry.get("updated_at") or ""),
                    actor=str(entry.get("actor") or ""),
                )
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "patrol_state_store: skip malformed entry name=%s err=%s",
                    name, exc,
                )
        return out

    def get(self, name: str) -> PatrolEnabledRecord | None:
        return self.snapshot().get(name)

    # ------------------------------------------------------------------ write

    def record(self, *, name: str, enabled: bool, actor: str = "system") -> None:
        """Upsert one patrol's enabled state, atomically.

        Atomic write contract: write to ``<path>.tmp`` then ``os.replace``.
        On POSIX and modern Windows this is rename-atomic, so a crash mid-
        write leaves either the old file or the new one — never a half-
        written one. We do **not** silently swallow OSError on the final
        rename: callers (``AgentRuntime.enable_patrol``) must know if the
        write failed so they can decide whether to refuse the lifecycle
        flip rather than lying to the LLM about success.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("patrol name must be non-empty")
        with self._lock:
            current = self._read_locked()
            current[clean_name] = PatrolEnabledRecord(
                name=clean_name,
                enabled=bool(enabled),
                updated_at=datetime.now(timezone.utc).isoformat(),
                actor=str(actor or "system"),
            )
            self._write_locked(current)

    def _write_locked(self, records: dict[str, PatrolEnabledRecord]) -> None:
        payload: dict[str, dict[str, Any]] = {
            name: {
                "enabled": rec.enabled,
                "updated_at": rec.updated_at,
                "actor": rec.actor,
            }
            for name, rec in records.items()
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
        except OSError:
            # Best-effort cleanup, then re-raise so caller can react.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


__all__ = ["PatrolEnabledRecord", "PatrolEnabledStateStore"]
