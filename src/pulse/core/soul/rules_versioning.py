from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_path(raw_path: str | None, *, default_path: Path) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return default_path
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


class GovernanceRulesVersionStore:
    """Version history for governance rules with rollback support."""

    def __init__(
        self,
        *,
        storage_path: str | None = None,
        max_versions: int = 500,
    ) -> None:
        default_path = Path.home() / ".pulse" / "governance_rules_versions.json"
        self._storage_path = _resolve_path(storage_path, default_path=default_path)
        self._max_versions = max(10, min(int(max_versions), 5000))
        self._lock = threading.Lock()
        self._versions: list[dict[str, Any]] = []
        self._load_versions()

    def record(
        self,
        *,
        rules: dict[str, Any],
        source: str,
        actor: str = "system",
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe: bool = True,
    ) -> dict[str, Any]:
        snapshot = self._normalize_rules(rules)
        safe_source = str(source or "").strip() or "unknown"
        safe_actor = str(actor or "").strip() or "system"
        payload_metadata = dict(metadata or {})
        with self._lock:
            if dedupe and self._versions:
                current_rules = self._versions[-1].get("rules")
                if isinstance(current_rules, dict) and current_rules == snapshot:
                    return deepcopy(self._versions[-1])
            entry = {
                "version_id": f"grv_{uuid.uuid4().hex[:14]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": safe_source,
                "actor": safe_actor,
                "note": str(note or "").strip(),
                "metadata": payload_metadata,
                "rules": snapshot,
            }
            self._versions.append(entry)
            if len(self._versions) > self._max_versions:
                self._versions = self._versions[-self._max_versions :]
            self._save_versions()
            return deepcopy(entry)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._versions:
                return None
            return deepcopy(self._versions[-1])

    def get(self, *, version_id: str) -> dict[str, Any] | None:
        safe_id = str(version_id or "").strip()
        if not safe_id:
            return None
        with self._lock:
            for item in self._versions:
                if str(item.get("version_id") or "") == safe_id:
                    return deepcopy(item)
        return None

    def list_versions(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        safe_cursor = self._parse_cursor(cursor)
        with self._lock:
            source = list(self._versions)
        source.reverse()
        total = len(source)
        items = source[safe_cursor : safe_cursor + safe_limit]
        next_cursor: str | None = None
        if safe_cursor + len(items) < total:
            next_cursor = str(safe_cursor + len(items))
        return {
            "total": total,
            "cursor": str(safe_cursor),
            "next_cursor": next_cursor,
            "items": [deepcopy(item) for item in items],
        }

    def resolve_compare_versions(
        self,
        *,
        from_version_id: str | None = None,
        to_version_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        with self._lock:
            versions = list(self._versions)
        if not versions:
            return None, None
        version_by_id = {str(item.get("version_id") or ""): item for item in versions}

        target_to: dict[str, Any] | None = None
        if str(to_version_id or "").strip():
            target_to = version_by_id.get(str(to_version_id).strip())
            if target_to is None:
                return None, None
        else:
            target_to = versions[-1]

        target_from: dict[str, Any] | None = None
        if str(from_version_id or "").strip():
            target_from = version_by_id.get(str(from_version_id).strip())
            if target_from is None:
                return None, None
        else:
            index = versions.index(target_to) if target_to in versions else (len(versions) - 1)
            if index <= 0:
                return None, target_to
            target_from = versions[index - 1]
        return deepcopy(target_from), deepcopy(target_to)

    def diff_versions(
        self,
        *,
        from_version_id: str | None = None,
        to_version_id: str | None = None,
    ) -> dict[str, Any]:
        from_item, to_item = self.resolve_compare_versions(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
        )
        if from_item is None or to_item is None:
            return {
                "ok": False,
                "reason": "versions not found or insufficient history",
                "from_version_id": str((from_item or {}).get("version_id") or ""),
                "to_version_id": str((to_item or {}).get("version_id") or ""),
                "changes": [],
                "summary": {"total": 0, "added": 0, "removed": 0, "updated": 0},
            }

        from_rules = self._normalize_rules(dict(from_item.get("rules") or {}))
        to_rules = self._normalize_rules(dict(to_item.get("rules") or {}))
        changes = self._diff_dicts(before=from_rules, after=to_rules, prefix="")
        summary = {
            "total": len(changes),
            "added": sum(1 for item in changes if item.get("change") == "added"),
            "removed": sum(1 for item in changes if item.get("change") == "removed"),
            "updated": sum(1 for item in changes if item.get("change") == "updated"),
        }
        return {
            "ok": True,
            "from_version_id": str(from_item.get("version_id") or ""),
            "to_version_id": str(to_item.get("version_id") or ""),
            "changes": changes,
            "summary": summary,
        }

    def count(self) -> int:
        with self._lock:
            return len(self._versions)

    def _load_versions(self) -> None:
        path = self._storage_path
        if not path.is_file():
            return
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(parsed, list):
            return
        rows: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            version_id = str(item.get("version_id") or "").strip()
            rules = item.get("rules")
            if not version_id or not isinstance(rules, dict):
                continue
            rows.append(item)
        with self._lock:
            self._versions = rows[-self._max_versions :]

    def _save_versions(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            json.dumps(self._versions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _normalize_rules(rules: dict[str, Any]) -> dict[str, Any]:
        payload = dict(rules or {})
        normalized = {
            "default_mode": str(payload.get("default_mode") or "autonomous"),
            "change_modes": dict(payload.get("change_modes") or {}),
            "risk_mode_overrides": dict(payload.get("risk_mode_overrides") or {}),
            "change_risk_mode_overrides": dict(payload.get("change_risk_mode_overrides") or {}),
        }
        return normalized

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        safe = str(cursor or "").strip()
        if not safe:
            return 0
        try:
            value = int(safe)
        except Exception:
            return 0
        return max(0, value)

    @classmethod
    def _diff_dicts(
        cls,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        prefix: str,
    ) -> list[dict[str, Any]]:
        keys = sorted(set(before.keys()).union(after.keys()))
        rows: list[dict[str, Any]] = []
        for key in keys:
            path = f"{prefix}.{key}" if prefix else str(key)
            has_before = key in before
            has_after = key in after
            before_value = before.get(key)
            after_value = after.get(key)

            if has_before and not has_after:
                rows.append({"path": path, "change": "removed", "before": before_value, "after": None})
                continue
            if has_after and not has_before:
                rows.append({"path": path, "change": "added", "before": None, "after": after_value})
                continue

            if isinstance(before_value, dict) and isinstance(after_value, dict):
                rows.extend(cls._diff_dicts(before=before_value, after=after_value, prefix=path))
                continue
            if before_value != after_value:
                rows.append({"path": path, "change": "updated", "before": before_value, "after": after_value})
        return rows
