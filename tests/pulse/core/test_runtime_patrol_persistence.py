"""Integration tests: AgentRuntime + PatrolEnabledStateStore.

These pin the post-2026-04-28 contract (post-mortem trace_753fecf70cc5):

* ``register_patrol`` rehydrates user lifecycle decisions from the store
  so a uvicorn reload / process restart never silently disarms a patrol
  the user explicitly enabled over IM.
* ``enable_patrol`` / ``disable_patrol`` write through to the store.
* A persistence failure surfaces as an exception (caller decides whether
  to claim success), never silently swallowed.

We deliberately exercise two real ``AgentRuntime`` instances back-to-back
against the same store path — that *is* the reload simulation.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pulse.core.runtime import AgentRuntime, RuntimeConfig
from pulse.core.scheduler import PatrolEnabledStateStore


def _make_runtime(store: PatrolEnabledStateStore | None) -> AgentRuntime:
    return AgentRuntime(config=RuntimeConfig(), patrol_state_store=store)


def _register_dummy(rt: AgentRuntime, name: str, *, enabled: bool = False) -> None:
    rt.register_patrol(
        name=name,
        handler=lambda ctx: None,
        peak_interval=60,
        offpeak_interval=120,
        enabled=enabled,
        active_hours_only=False,
        token_budget=1000,
    )


def test_enable_patrol_persists_to_disk(tmp_path: Path) -> None:
    store = PatrolEnabledStateStore(path=tmp_path / "patrol_state.json")
    rt = _make_runtime(store)
    _register_dummy(rt, "alpha")

    assert rt.enable_patrol("alpha", actor="im:user-x") is True

    rec = store.get("alpha")
    assert rec is not None
    assert rec.enabled is True
    assert rec.actor == "im:user-x"


def test_disable_patrol_persists_disabled_state(tmp_path: Path) -> None:
    """Disabled state matters too — otherwise rehydrate could turn a
    paused patrol back on after restart."""
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    rt = _make_runtime(store)
    _register_dummy(rt, "alpha", enabled=True)

    assert rt.disable_patrol("alpha", actor="im:user-y") is True

    rec = store.get("alpha")
    assert rec is not None
    assert rec.enabled is False
    assert rec.actor == "im:user-y"


def test_register_patrol_rehydrates_enabled_from_store(tmp_path: Path) -> None:
    """Reload simulation: instance A enabled the patrol; instance B (a
    fresh runtime pointed at the same store) must boot it ON."""
    path = tmp_path / "p.json"
    store_a = PatrolEnabledStateStore(path=path)
    rt_a = _make_runtime(store_a)
    _register_dummy(rt_a, "alpha", enabled=False)
    rt_a.enable_patrol("alpha", actor="im:user-x")

    store_b = PatrolEnabledStateStore(path=path)
    rt_b = _make_runtime(store_b)
    _register_dummy(rt_b, "alpha", enabled=False)

    snapshot = rt_b.get_patrol_stats("alpha")
    assert snapshot is not None
    assert snapshot["enabled"] is True, (
        "register_patrol must honor the persisted enabled=True flag "
        "so reload doesn't silently disarm the running service"
    )


def test_register_patrol_rehydrates_disabled_overrides_module_default(
    tmp_path: Path,
) -> None:
    """A user-paused patrol must stay paused after restart even if the
    module passes ``enabled=True`` at registration time. (Modules calling
    ``register_patrol(enabled=True)`` is a test-only escape hatch but
    we still want the user's override to win for safety.)"""
    path = tmp_path / "p.json"
    store_a = PatrolEnabledStateStore(path=path)
    rt_a = _make_runtime(store_a)
    _register_dummy(rt_a, "alpha", enabled=True)
    rt_a.disable_patrol("alpha", actor="im:user-x")

    store_b = PatrolEnabledStateStore(path=path)
    rt_b = _make_runtime(store_b)
    _register_dummy(rt_b, "alpha", enabled=True)

    snapshot = rt_b.get_patrol_stats("alpha")
    assert snapshot is not None
    assert snapshot["enabled"] is False


def test_register_patrol_uses_module_default_when_store_has_no_record(
    tmp_path: Path,
) -> None:
    """No prior user decision → fall back to the module's default
    (always False per ADR-004 §6.1.1)."""
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    rt = _make_runtime(store)
    _register_dummy(rt, "alpha", enabled=False)

    snapshot = rt.get_patrol_stats("alpha")
    assert snapshot is not None
    assert snapshot["enabled"] is False
    assert store.snapshot() == {}, "no record should exist for a fresh boot"


def test_runtime_without_state_store_still_works(tmp_path: Path) -> None:
    """Backwards-compat / unit-test escape hatch: passing ``None`` keeps
    the legacy in-memory-only behavior. enable / disable still flip the
    flag, just don't persist."""
    rt = _make_runtime(None)
    _register_dummy(rt, "alpha", enabled=False)

    assert rt.enable_patrol("alpha") is True
    assert rt.get_patrol_stats("alpha")["enabled"] is True
    assert rt.disable_patrol("alpha") is True
    assert rt.get_patrol_stats("alpha")["enabled"] is False


def test_persistence_failure_propagates_from_enable(tmp_path: Path) -> None:
    """If the store cannot write, ``enable_patrol`` must raise — never
    return True with no on-disk record. That return value is exactly
    what the LLM uses to claim 'enabled' in the IM reply; lying here is
    how the post-mortem bug survived."""
    store = PatrolEnabledStateStore(path=tmp_path / "p.json")
    rt = _make_runtime(store)
    _register_dummy(rt, "alpha")

    with patch.object(store, "record", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            rt.enable_patrol("alpha", actor="im:user-x")
