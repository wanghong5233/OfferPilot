from __future__ import annotations

from pulse.core.soul.rules_versioning import GovernanceRulesVersionStore


def _sample_rules(default_mode: str = "autonomous") -> dict[str, object]:
    return {
        "default_mode": default_mode,
        "change_modes": {"prefs_update": "autonomous", "soul_update": "supervised"},
        "risk_mode_overrides": {"critical": "gated"},
        "change_risk_mode_overrides": {"soul_update": {"high": "supervised", "critical": "gated"}},
    }


def test_rules_version_store_record_list_get(tmp_path) -> None:
    store = GovernanceRulesVersionStore(storage_path=str(tmp_path / "rules_versions.json"))
    v1 = store.record(rules=_sample_rules("autonomous"), source="startup", dedupe=False)
    v2 = store.record(rules=_sample_rules("supervised"), source="reload", dedupe=False)
    assert v1["version_id"] != v2["version_id"]
    assert store.count() == 2

    latest = store.latest()
    assert latest is not None
    assert latest["version_id"] == v2["version_id"]

    fetched = store.get(version_id=v1["version_id"])
    assert fetched is not None
    assert fetched["rules"]["default_mode"] == "autonomous"

    page = store.list_versions(limit=1, cursor=None)
    assert page["total"] == 2
    assert page["next_cursor"] == "1"
    assert len(page["items"]) == 1

    diff = store.diff_versions(from_version_id=v1["version_id"], to_version_id=v2["version_id"])
    assert diff["ok"] is True
    assert diff["summary"]["total"] >= 1
    assert any(item["path"] == "default_mode" for item in diff["changes"])
