from __future__ import annotations

from pulse.core.memory.core_memory import CoreMemory


def test_core_memory_update_preference_and_persist(tmp_path) -> None:
    soul_path = tmp_path / "soul.yaml"
    soul_path.write_text(
        "soul:\n"
        "  assistant_prefix: Pulse\n"
        "  tone: calm\n",
        encoding="utf-8",
    )
    storage_path = tmp_path / "core_memory.json"
    memory = CoreMemory(
        storage_path=str(storage_path),
        soul_config_path=str(soul_path),
    )
    memory.update_preferences({"default_location": "hangzhou", "dislike": "game company"})
    snapshot = memory.snapshot()
    assert snapshot["prefs"]["default_location"] == "hangzhou"
    assert snapshot["prefs"]["dislike"] == "game company"

    restored = CoreMemory(
        storage_path=str(storage_path),
        soul_config_path=str(soul_path),
    )
    assert restored.preference("default_location") == "hangzhou"
    prompt = restored.build_system_prompt(max_chars=600)
    assert "hangzhou" in prompt
