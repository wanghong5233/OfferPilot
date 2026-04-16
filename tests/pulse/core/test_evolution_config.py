from __future__ import annotations

import json

from pulse.core.evolution_config import build_evolution_governance_options


def test_build_evolution_governance_options_from_file(tmp_path) -> None:
    config_path = tmp_path / "evolution_rules.json"
    config_path.write_text(
        json.dumps(
            {
                "default_mode": "supervised",
                "change_modes": {"prefs_update": "autonomous", "soul_update": "gated"},
                "risk_mode_overrides": {"high": "supervised", "critical": "gated"},
                "change_risk_mode_overrides": {"prefs_update": {"high": "gated"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    options = build_evolution_governance_options(config_path=str(config_path))
    assert options["default_mode"] == "supervised"
    assert options["change_modes"]["soul_update"] == "gated"
    assert options["risk_mode_overrides"]["critical"] == "gated"
    assert options["change_risk_mode_overrides"]["prefs_update"]["high"] == "gated"


def test_build_evolution_governance_options_override_change_modes(tmp_path) -> None:
    config_path = tmp_path / "evolution_rules.json"
    config_path.write_text(json.dumps({"default_mode": "autonomous"}, ensure_ascii=False), encoding="utf-8")

    options = build_evolution_governance_options(
        config_path=str(config_path),
        default_mode_override="supervised",
        change_mode_overrides={"soul_update": "autonomous"},
    )
    assert options["default_mode"] == "supervised"
    assert options["change_modes"]["soul_update"] == "autonomous"
