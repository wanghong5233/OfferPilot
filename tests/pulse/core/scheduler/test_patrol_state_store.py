"""Tests for ``PatrolEnabledStateStore``.

Covers the durable contract that prevents the 2026-04-28 regression
(post-mortem ``trace_753fecf70cc5``): bot tells user "已开启自动投递",
uvicorn watchfiles reloads, in-memory ``ScheduleTask.enabled`` resets to
False, the long-running service is silently dead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pulse.core.scheduler.state_store import (
    PatrolEnabledRecord,
    PatrolEnabledStateStore,
)


def test_snapshot_returns_empty_when_file_does_not_exist(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "patrol_state.json")
    assert store.snapshot() == {}
    assert store.get("anything") is None


def test_record_then_snapshot_roundtrips_enabled_state(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "patrol_state.json")
    store.record(name="job_greet.patrol", enabled=True, actor="im:test")
    store.record(name="job_chat.patrol", enabled=False, actor="rest")

    snapshot = store.snapshot()
    assert set(snapshot.keys()) == {"job_greet.patrol", "job_chat.patrol"}
    assert snapshot["job_greet.patrol"].enabled is True
    assert snapshot["job_greet.patrol"].actor == "im:test"
    assert snapshot["job_chat.patrol"].enabled is False
    assert snapshot["job_chat.patrol"].actor == "rest"


def test_record_persists_across_store_instances(tmp_path: Path) -> None:
    """Surviving a process restart is the whole point. New store
    instance pointed at the same path must see prior writes."""
    path = tmp_path / "patrol_state.json"
    s1 = PatrolEnabledStateStore(path=path)
    s1.record(name="alpha", enabled=True, actor="im:user-x")

    s2 = PatrolEnabledStateStore(path=path)
    rec = s2.get("alpha")
    assert rec is not None
    assert rec.enabled is True
    assert rec.actor == "im:user-x"


def test_record_overwrites_prior_value_for_same_name(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    store.record(name="alpha", enabled=True, actor="a")
    store.record(name="alpha", enabled=False, actor="b")

    rec = store.get("alpha")
    assert rec is not None
    assert rec.enabled is False
    assert rec.actor == "b"


def test_record_rejects_empty_name(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    with pytest.raises(ValueError):
        store.record(name="   ", enabled=True)


def test_corrupt_file_does_not_crash_and_is_left_intact(tmp_path: Path) -> None:
    """If someone hand-edited the file into broken JSON, the store
    must not delete it (manual recovery beats auto-truncation) and
    must not crash the boot path that calls snapshot()."""
    path = tmp_path / "patrol_state.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = PatrolEnabledStateStore(path=path)
    assert store.snapshot() == {}
    assert path.read_text(encoding="utf-8") == "{ this is not json"


def test_atomic_write_does_not_leave_tmp_files_behind(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    store.record(name="alpha", enabled=True)
    store.record(name="beta", enabled=False)

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith("p.json.")]
    assert leftovers == [], f"unexpected tmp files: {leftovers}"


def test_snapshot_skips_malformed_entries_but_keeps_valid_ones(tmp_path: Path) -> None:
    """Defense-in-depth: a partially corrupt file (e.g. one entry has
    a non-dict value) should still surface every clean record so the
    runtime can rehydrate the rest."""
    path = tmp_path / "p.json"
    path.write_text(
        '{"alpha": {"enabled": true, "updated_at": "x", "actor": "y"},'
        ' "beta": "not-a-dict"}',
        encoding="utf-8",
    )
    store = PatrolEnabledStateStore(path=path)
    snap = store.snapshot()
    assert "alpha" in snap and snap["alpha"].enabled is True
    assert "beta" not in snap


def test_record_dataclass_is_frozen_and_immutable() -> None:
    rec = PatrolEnabledRecord(
        name="alpha", enabled=True, updated_at="2026-04-28T00:00:00+00:00", actor="t",
    )
    with pytest.raises(Exception):
        rec.enabled = False  # type: ignore[misc]
