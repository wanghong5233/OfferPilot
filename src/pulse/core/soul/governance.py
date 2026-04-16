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


class SoulGovernance:
    """Govern mutable belief updates with audit trail and rollback."""

    CORE_SOUL_KEYS = {"assistant_prefix", "principles"}
    MUTABLE_SOUL_KEYS = {"tone", "style_rules"}
    VALID_MODES = {"autonomous", "supervised", "gated"}
    VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
    DEFAULT_CHANGE_MODES = {
        "prefs_update": "autonomous",
        "soul_update": "supervised",
        "belief_mutation": "autonomous",
    }
    DEFAULT_RISK_MODE_OVERRIDES = {
        "critical": "gated",
    }
    DEFAULT_CHANGE_RISK_OVERRIDES = {
        "soul_update": {"high": "supervised", "critical": "gated"},
    }

    def __init__(
        self,
        *,
        core_memory: Any,
        audit_path: str | None = None,
        default_mode: str = "autonomous",
        change_modes: dict[str, str] | None = None,
        risk_mode_overrides: dict[str, str] | None = None,
        change_risk_mode_overrides: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._core_memory = core_memory
        default_path = Path.home() / ".pulse" / "governance_audit.json"
        self._audit_path = _resolve_path(audit_path, default_path=default_path)
        self._lock = threading.Lock()
        self._audits: list[dict[str, Any]] = []
        self._default_mode = self._normalize_mode(default_mode, fallback="autonomous")
        merged_modes = dict(self.DEFAULT_CHANGE_MODES)
        for key, value in dict(change_modes or {}).items():
            merged_modes[str(key)] = self._normalize_mode(value, fallback=merged_modes.get(str(key), self._default_mode))
        self._change_modes = merged_modes
        merged_risk_modes = dict(self.DEFAULT_RISK_MODE_OVERRIDES)
        for key, value in dict(risk_mode_overrides or {}).items():
            risk = self._normalize_risk(str(key), fallback="")
            if not risk:
                continue
            merged_risk_modes[risk] = self._normalize_mode(value, fallback=merged_risk_modes.get(risk, self._default_mode))
        self._risk_mode_overrides = merged_risk_modes
        merged_change_risk = self._normalize_change_risk_mode_overrides(self.DEFAULT_CHANGE_RISK_OVERRIDES)
        for change_type, mapping in dict(change_risk_mode_overrides or {}).items():
            safe_type = str(change_type or "").strip()
            if not safe_type:
                continue
            current = dict(merged_change_risk.get(safe_type) or {})
            for risk_key, mode_value in dict(mapping or {}).items():
                risk = self._normalize_risk(str(risk_key), fallback="")
                if not risk:
                    continue
                current[risk] = self._normalize_mode(mode_value, fallback=current.get(risk, self._default_mode))
            if current:
                merged_change_risk[safe_type] = current
        self._change_risk_mode_overrides = merged_change_risk
        self._load_audits()

    def mode_status(self) -> dict[str, Any]:
        return {
            "default_mode": self._default_mode,
            "change_modes": dict(self._change_modes),
            "risk_mode_overrides": dict(self._risk_mode_overrides),
            "change_risk_mode_overrides": self._normalize_change_risk_mode_overrides(self._change_risk_mode_overrides),
        }

    def assess_change(
        self,
        *,
        change_type: str,
        risk_level: str,
        source: str,
        actor: str = "runtime",
        payload: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        safe_type = str(change_type or "").strip() or "unknown_change"
        safe_risk = self._normalize_risk(risk_level, fallback="medium")
        mode, mode_reason = self._resolve_mode_details(
            change_type=safe_type,
            risk_level=safe_risk,
        )
        entry = self._append_audit(
            {
                "status": "assessed",
                "mode": mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "type": safe_type,
                "source": source,
                "actor": actor,
                "reason": str(reason or "").strip(),
                "payload": dict(payload or {}),
            }
        )
        return {
            "ok": mode == "autonomous",
            "status": "allowed" if mode == "autonomous" else "blocked_by_governance",
            "mode": mode,
            "mode_reason": mode_reason,
            "risk_level": safe_risk,
            "change_id": entry["change_id"],
            "reason": entry.get("reason") or mode_reason,
        }

    def replace_modes(
        self,
        *,
        default_mode: str,
        change_modes: dict[str, str] | None = None,
        risk_mode_overrides: dict[str, str] | None = None,
        change_risk_mode_overrides: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        safe_default = self._normalize_mode(default_mode, fallback=self._default_mode)
        merged_change_modes = dict(self.DEFAULT_CHANGE_MODES)
        for key, value in dict(change_modes or {}).items():
            safe_key = str(key or "").strip()
            if not safe_key:
                continue
            merged_change_modes[safe_key] = self._normalize_mode(
                value,
                fallback=merged_change_modes.get(safe_key, safe_default),
            )

        merged_risk_modes = dict(self.DEFAULT_RISK_MODE_OVERRIDES)
        for key, value in dict(risk_mode_overrides or {}).items():
            safe_risk = self._normalize_risk(str(key), fallback="")
            if not safe_risk:
                continue
            merged_risk_modes[safe_risk] = self._normalize_mode(
                value,
                fallback=merged_risk_modes.get(safe_risk, safe_default),
            )

        merged_change_risk = self._normalize_change_risk_mode_overrides(self.DEFAULT_CHANGE_RISK_OVERRIDES)
        for change_type, mapping in dict(change_risk_mode_overrides or {}).items():
            safe_type = str(change_type or "").strip()
            if not safe_type:
                continue
            row = dict(merged_change_risk.get(safe_type) or {})
            for risk_key, mode_value in dict(mapping or {}).items():
                safe_risk = self._normalize_risk(str(risk_key), fallback="")
                if not safe_risk:
                    continue
                row[safe_risk] = self._normalize_mode(mode_value, fallback=row.get(safe_risk, safe_default))
            if row:
                merged_change_risk[safe_type] = row

        with self._lock:
            self._default_mode = safe_default
            self._change_modes = merged_change_modes
            self._risk_mode_overrides = merged_risk_modes
            self._change_risk_mode_overrides = merged_change_risk
        return self.mode_status()

    def set_mode(
        self,
        *,
        mode: str,
        change_type: str | None = None,
        risk_level: str | None = None,
    ) -> dict[str, Any]:
        safe_mode = self._normalize_mode(mode, fallback=self._default_mode)
        safe_type = str(change_type or "").strip()
        safe_risk = self._normalize_risk(str(risk_level or ""), fallback="")
        with self._lock:
            if safe_type and safe_risk:
                mapping = dict(self._change_risk_mode_overrides.get(safe_type) or {})
                mapping[safe_risk] = safe_mode
                self._change_risk_mode_overrides[safe_type] = mapping
            elif safe_risk:
                self._risk_mode_overrides[safe_risk] = safe_mode
            elif safe_type:
                self._change_modes[safe_type] = safe_mode
            else:
                self._default_mode = safe_mode
        return self.mode_status()

    def apply_preference_updates(
        self,
        *,
        updates: dict[str, Any],
        source: str,
        actor: str = "evolution_engine",
        mode: str | None = None,
        risk_level: str = "medium",
    ) -> dict[str, Any]:
        safe_updates = dict(updates or {})
        if not safe_updates:
            return {"ok": False, "status": "noop", "reason": "empty updates"}
        safe_risk = self._normalize_risk(risk_level, fallback="medium")
        change_mode, mode_reason = self._resolve_mode_details(
            change_type="prefs_update",
            override_mode=mode,
            risk_level=safe_risk,
        )
        if change_mode == "supervised":
            entry = self._append_audit(
                {
                    "status": "pending_approval",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "prefs_update",
                    "source": source,
                    "actor": actor,
                    "updates": safe_updates,
                }
            )
            return {
                "ok": False,
                "status": "pending_approval",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "needs_approval": True,
            }
        if change_mode == "gated":
            entry = self._append_audit(
                {
                    "status": "blocked_by_gate",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "prefs_update",
                    "source": source,
                    "actor": actor,
                    "reason": "change blocked by governance gate",
                    "updates": safe_updates,
                }
            )
            return {
                "ok": False,
                "status": "blocked_by_gate",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "reason": entry["reason"],
            }
        before: dict[str, Any] = {}
        for key in safe_updates.keys():
            before[str(key)] = self._core_memory.preference(str(key), None)
        try:
            after = self._core_memory.update_preferences(safe_updates)
            entry = self._append_audit(
                {
                    "status": "applied",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "prefs_update",
                    "source": source,
                    "actor": actor,
                    "before": before,
                    "after": {k: after.get(k) for k in safe_updates.keys()},
                    "updates": safe_updates,
                }
            )
            return {
                "ok": True,
                "status": "applied",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "updated": safe_updates,
            }
        except Exception as exc:
            entry = self._append_audit(
                {
                    "status": "rejected",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "prefs_update",
                    "source": source,
                    "actor": actor,
                    "reason": str(exc)[:300],
                    "updates": safe_updates,
                }
            )
            return {
                "ok": False,
                "status": "rejected",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "reason": entry.get("reason", "unknown"),
            }

    def apply_soul_update(
        self,
        *,
        updates: dict[str, Any],
        source: str,
        actor: str = "evolution_engine",
        mode: str | None = None,
        risk_level: str = "high",
    ) -> dict[str, Any]:
        safe_updates = dict(updates or {})
        if not safe_updates:
            return {"ok": False, "status": "noop", "reason": "empty updates"}
        safe_risk = self._normalize_risk(risk_level, fallback="high")
        change_mode, mode_reason = self._resolve_mode_details(
            change_type="soul_update",
            override_mode=mode,
            risk_level=safe_risk,
        )
        core_hits = sorted(self.CORE_SOUL_KEYS.intersection(safe_updates.keys()))
        if core_hits:
            entry = self._append_audit(
                {
                    "status": "rejected",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "soul_update",
                    "source": source,
                    "actor": actor,
                    "reason": f"core soul keys are immutable: {core_hits}",
                    "updates": safe_updates,
                }
            )
            return {
                "ok": False,
                "status": "rejected",
                "change_id": entry["change_id"],
                "reason": entry["reason"],
            }

        filtered = {k: v for k, v in safe_updates.items() if k in self.MUTABLE_SOUL_KEYS}
        if not filtered:
            return {"ok": False, "status": "noop", "reason": "no mutable soul keys"}
        if change_mode == "supervised":
            entry = self._append_audit(
                {
                    "status": "pending_approval",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "soul_update",
                    "source": source,
                    "actor": actor,
                    "updates": filtered,
                }
            )
            return {
                "ok": False,
                "status": "pending_approval",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "needs_approval": True,
            }
        if change_mode == "gated":
            entry = self._append_audit(
                {
                    "status": "blocked_by_gate",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "soul_update",
                    "source": source,
                    "actor": actor,
                    "reason": "change blocked by governance gate",
                    "updates": filtered,
                }
            )
            return {
                "ok": False,
                "status": "blocked_by_gate",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "reason": entry["reason"],
            }

        current_soul = self._core_memory.read_block("soul")
        before = {k: (current_soul or {}).get(k) for k in filtered.keys()} if isinstance(current_soul, dict) else {}
        try:
            updated = self._core_memory.update_block(block="soul", content=filtered, merge=True)
            entry = self._append_audit(
                {
                    "status": "applied",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "soul_update",
                    "source": source,
                    "actor": actor,
                    "before": before,
                    "after": {k: updated.get(k) for k in filtered.keys()},
                    "updates": filtered,
                }
            )
            return {
                "ok": True,
                "status": "applied",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "updated": filtered,
            }
        except Exception as exc:
            entry = self._append_audit(
                {
                    "status": "rejected",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "soul_update",
                    "source": source,
                    "actor": actor,
                    "reason": str(exc)[:300],
                    "updates": filtered,
                }
            )
            return {
                "ok": False,
                "status": "rejected",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "reason": entry.get("reason", "unknown"),
            }

    def add_mutable_belief(
        self,
        *,
        belief: str,
        source: str,
        actor: str = "evolution_engine",
        mode: str | None = None,
        risk_level: str = "low",
    ) -> dict[str, Any]:
        safe_belief = str(belief or "").strip()
        if not safe_belief:
            return {"ok": False, "status": "noop", "reason": "belief is empty"}
        safe_risk = self._normalize_risk(risk_level, fallback="low")
        change_mode, mode_reason = self._resolve_mode_details(
            change_type="belief_mutation",
            override_mode=mode,
            risk_level=safe_risk,
        )
        context = self._core_memory.read_block("context")
        if not isinstance(context, dict):
            context = {}
        beliefs = context.get("beliefs")
        if not isinstance(beliefs, dict):
            beliefs = {"core": [], "mutable": []}
        core_items = list(beliefs.get("core") or [])
        mutable_items = [str(item) for item in list(beliefs.get("mutable") or []) if str(item).strip()]
        if safe_belief in core_items:
            entry = self._append_audit(
                {
                    "status": "rejected",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "belief_mutation",
                    "source": source,
                    "actor": actor,
                    "reason": "cannot mutate CORE beliefs",
                    "target": "core",
                    "belief": safe_belief,
                }
            )
            return {"ok": False, "status": "rejected", "change_id": entry["change_id"], "reason": entry["reason"]}

        if safe_belief in mutable_items:
            return {"ok": True, "status": "noop", "reason": "belief already exists"}
        if change_mode == "supervised":
            entry = self._append_audit(
                {
                    "status": "pending_approval",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "belief_mutation",
                    "source": source,
                    "actor": actor,
                    "target": "mutable",
                    "belief": safe_belief,
                }
            )
            return {
                "ok": False,
                "status": "pending_approval",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "needs_approval": True,
            }
        if change_mode == "gated":
            entry = self._append_audit(
                {
                    "status": "blocked_by_gate",
                    "mode": change_mode,
                    "mode_reason": mode_reason,
                    "risk_level": safe_risk,
                    "type": "belief_mutation",
                    "source": source,
                    "actor": actor,
                    "reason": "change blocked by governance gate",
                    "target": "mutable",
                    "belief": safe_belief,
                }
            )
            return {
                "ok": False,
                "status": "blocked_by_gate",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "change_id": entry["change_id"],
                "reason": entry["reason"],
            }

        before = list(mutable_items)
        mutable_items.append(safe_belief)
        payload = {"beliefs": {"mutable": mutable_items}}
        updated_context = self._core_memory.update_block(block="context", content=payload, merge=True)
        after_items = (
            list((updated_context.get("beliefs") or {}).get("mutable") or [])
            if isinstance(updated_context, dict)
            else list(mutable_items)
        )
        entry = self._append_audit(
            {
                "status": "applied",
                "mode": change_mode,
                "mode_reason": mode_reason,
                "risk_level": safe_risk,
                "type": "belief_mutation",
                "source": source,
                "actor": actor,
                "target": "mutable",
                "belief": safe_belief,
                "before": before,
                "after": list(after_items),
            }
        )
        return {
            "ok": True,
            "status": "applied",
            "mode": change_mode,
            "mode_reason": mode_reason,
            "risk_level": safe_risk,
            "change_id": entry["change_id"],
            "belief": safe_belief,
        }

    def approve_change(self, *, change_id: str, actor: str = "operator") -> dict[str, Any]:
        safe_id = str(change_id or "").strip()
        if not safe_id:
            raise ValueError("change_id is required")
        with self._lock:
            target = self._find_audit_locked(safe_id)
            if target is None:
                raise KeyError(f"change_id not found: {safe_id}")
            if str(target.get("status") or "") != "pending_approval":
                return {"ok": False, "status": "noop", "reason": "only pending_approval can be approved"}
            change_type = str(target.get("type") or "")
            source = str(target.get("source") or "approved_change")

        try:
            if change_type == "prefs_update":
                updates = dict(target.get("updates") or {})
                before = {str(key): self._core_memory.preference(str(key), None) for key in updates.keys()}
                after = self._core_memory.update_preferences(updates)
                patch = {
                    "before": before,
                    "after": {k: after.get(k) for k in updates.keys()},
                    "updates": updates,
                }
            elif change_type == "soul_update":
                updates = dict(target.get("updates") or {})
                current_soul = self._core_memory.read_block("soul")
                before = (
                    {k: (current_soul or {}).get(k) for k in updates.keys()}
                    if isinstance(current_soul, dict)
                    else {}
                )
                after = self._core_memory.update_block(block="soul", content=updates, merge=True)
                patch = {
                    "before": before,
                    "after": {k: after.get(k) for k in updates.keys()},
                    "updates": updates,
                }
            elif change_type == "belief_mutation":
                safe_belief = str(target.get("belief") or "").strip()
                context = self._core_memory.read_block("context")
                if not isinstance(context, dict):
                    context = {}
                beliefs = context.get("beliefs")
                if not isinstance(beliefs, dict):
                    beliefs = {"core": [], "mutable": []}
                mutable_items = [str(item) for item in list(beliefs.get("mutable") or []) if str(item).strip()]
                before = list(mutable_items)
                if safe_belief and safe_belief not in mutable_items:
                    mutable_items.append(safe_belief)
                    self._core_memory.update_block(
                        block="context",
                        content={"beliefs": {"mutable": mutable_items}},
                        merge=True,
                    )
                patch = {
                    "before": before,
                    "after": list(mutable_items),
                    "belief": safe_belief,
                }
            else:
                return {"ok": False, "status": "noop", "reason": f"unsupported change type: {change_type}"}
        except Exception as exc:
            with self._lock:
                target = self._find_audit_locked(safe_id)
                if target is None:
                    raise KeyError(f"change_id not found: {safe_id}")
                target["status"] = "rejected"
                target["approved_by"] = actor
                target["approved_at"] = datetime.now(timezone.utc).isoformat()
                target["reason"] = str(exc)[:300]
                self._save_audits()
            return {
                "ok": False,
                "status": "rejected",
                "change_id": safe_id,
                "reason": str(exc)[:300],
            }

        with self._lock:
            target = self._find_audit_locked(safe_id)
            if target is None:
                raise KeyError(f"change_id not found: {safe_id}")
            target["status"] = "applied"
            target["source"] = source
            target["approved_by"] = actor
            target["approved_at"] = datetime.now(timezone.utc).isoformat()
            target.update(patch)
            self._save_audits()
        return {"ok": True, "status": "applied", "change_id": safe_id}

    def rollback(self, *, change_id: str, actor: str = "operator") -> dict[str, Any]:
        safe_id = str(change_id or "").strip()
        if not safe_id:
            raise ValueError("change_id is required")
        with self._lock:
            target = None
            for item in self._audits:
                if str(item.get("change_id") or "") == safe_id:
                    target = item
                    break
            if target is None:
                raise KeyError(f"change_id not found: {safe_id}")
            if str(target.get("status") or "") != "applied":
                return {"ok": False, "status": "noop", "reason": "only applied changes can rollback"}

            change_type = str(target.get("type") or "")
            if change_type == "prefs_update":
                before = dict(target.get("before") or {})
                self._core_memory.update_preferences(before)
            elif change_type == "soul_update":
                before = dict(target.get("before") or {})
                self._core_memory.update_block(block="soul", content=before, merge=True)
            elif change_type == "belief_mutation":
                before = list(target.get("before") or [])
                self._core_memory.update_block(block="context", content={"beliefs": {"mutable": before}}, merge=True)
            else:
                return {"ok": False, "status": "noop", "reason": f"unsupported change type: {change_type}"}

            target["status"] = "rolled_back"
            target["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            target["rolled_back_by"] = actor
            self._save_audits()
            return {"ok": True, "status": "rolled_back", "change_id": safe_id}

    def list_audits(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        change_type: str | None = None,
        mode: str | None = None,
        risk_level: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 5000))
        safe_status = str(status or "").strip().lower()
        safe_type = str(change_type or "").strip().lower()
        safe_mode = str(mode or "").strip().lower()
        safe_risk = str(risk_level or "").strip().lower()
        with self._lock:
            source = list(self._audits)
        source.reverse()
        rows: list[dict[str, Any]] = []
        for item in source:
            if safe_status and str(item.get("status") or "").lower() != safe_status:
                continue
            if safe_type and str(item.get("type") or "").lower() != safe_type:
                continue
            if safe_mode and str(item.get("mode") or "").lower() != safe_mode:
                continue
            if safe_risk and str(item.get("risk_level") or "").lower() != safe_risk:
                continue
            rows.append(deepcopy(item))
            if len(rows) >= safe_limit:
                break
        return rows

    def audit_stats(self, *, window_hours: int = 24) -> dict[str, Any]:
        safe_window = max(1, min(int(window_hours), 24 * 30))
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - safe_window * 3600
        with self._lock:
            source = list(self._audits)

        total = len(source)
        in_window = 0
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_risk: dict[str, int] = {}

        for item in source:
            status = str(item.get("status") or "unknown").strip().lower() or "unknown"
            change_type = str(item.get("type") or "unknown").strip().lower() or "unknown"
            mode = str(item.get("mode") or "unknown").strip().lower() or "unknown"
            risk = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
            by_status[status] = by_status.get(status, 0) + 1
            by_type[change_type] = by_type.get(change_type, 0) + 1
            by_mode[mode] = by_mode.get(mode, 0) + 1
            by_risk[risk] = by_risk.get(risk, 0) + 1

            ts_raw = str(item.get("timestamp") or "").strip()
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts >= cutoff:
                in_window += 1

        return {
            "total": total,
            "window_hours": safe_window,
            "in_window": in_window,
            "by_status": by_status,
            "by_type": by_type,
            "by_mode": by_mode,
            "by_risk": by_risk,
        }

    def _load_audits(self) -> None:
        path = self._audit_path
        if not path.is_file():
            return
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(parsed, list):
            return
        rows = [item for item in parsed if isinstance(item, dict)]
        with self._lock:
            self._audits = rows[-10000:]

    def _save_audits(self) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit_path.write_text(json.dumps(self._audits, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "change_id": f"chg_{uuid.uuid4().hex[:14]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dict(payload),
        }
        with self._lock:
            self._audits.append(entry)
            if len(self._audits) > 10000:
                self._audits = self._audits[-10000:]
            self._save_audits()
        return deepcopy(entry)

    def _resolve_mode_details(
        self,
        *,
        change_type: str,
        override_mode: str | None = None,
        risk_level: str | None = None,
    ) -> tuple[str, str]:
        safe_change_type = str(change_type or "").strip()
        safe_risk = self._normalize_risk(str(risk_level or ""), fallback="")
        if override_mode is not None and str(override_mode).strip():
            resolved = self._normalize_mode(str(override_mode), fallback=self._default_mode)
            return resolved, "explicit_override"
        with self._lock:
            type_map = dict(self._change_risk_mode_overrides.get(safe_change_type) or {})
            if safe_risk and safe_risk in type_map:
                resolved = self._normalize_mode(type_map[safe_risk], fallback=self._default_mode)
                return resolved, f"change_risk_override:{safe_change_type}:{safe_risk}"
            if safe_risk and safe_risk in self._risk_mode_overrides:
                resolved = self._normalize_mode(self._risk_mode_overrides[safe_risk], fallback=self._default_mode)
                return resolved, f"risk_override:{safe_risk}"
            if safe_change_type in self._change_modes:
                resolved = self._normalize_mode(self._change_modes[safe_change_type], fallback=self._default_mode)
                return resolved, f"change_default:{safe_change_type}"
            return self._default_mode, "global_default"

    def _normalize_mode(self, mode: str, *, fallback: str) -> str:
        safe_mode = str(mode or "").strip().lower()
        if safe_mode in self.VALID_MODES:
            return safe_mode
        return str(fallback or "autonomous").strip().lower() or "autonomous"

    def _normalize_risk(self, risk_level: str, *, fallback: str) -> str:
        safe_risk = str(risk_level or "").strip().lower()
        if safe_risk in self.VALID_RISK_LEVELS:
            return safe_risk
        safe_fallback = str(fallback or "").strip().lower()
        if safe_fallback in self.VALID_RISK_LEVELS:
            return safe_fallback
        return ""

    def _normalize_change_risk_mode_overrides(
        self,
        mapping: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        rows: dict[str, dict[str, str]] = {}
        for change_type, change_map in dict(mapping or {}).items():
            safe_change_type = str(change_type or "").strip()
            if not safe_change_type:
                continue
            normalized: dict[str, str] = {}
            for risk_key, mode_value in dict(change_map or {}).items():
                risk = self._normalize_risk(str(risk_key), fallback="")
                if not risk:
                    continue
                normalized[risk] = self._normalize_mode(mode_value, fallback=self._default_mode)
            if normalized:
                rows[safe_change_type] = normalized
        return rows

    def _find_audit_locked(self, change_id: str) -> dict[str, Any] | None:
        safe_id = str(change_id or "").strip()
        for item in self._audits:
            if str(item.get("change_id") or "") == safe_id:
                return item
        return None
