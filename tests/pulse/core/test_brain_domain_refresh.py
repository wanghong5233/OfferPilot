"""Regression: ``Brain._render_domain_snapshot_refresh`` must accept the
``list[PromptSection]`` returned by ``PromptContractBuilder._render_domain_snapshots``.

Bug 2026-04-28 (trace_226e7669aa1a): mutating tools (e.g.
``system.patrol.enable``) trigger a snapshot refresh after each successful
call. The refresh path used to do ``"\n\n".join(sections)`` directly on the
typed sections, raising ``TypeError: sequence item 0: expected str instance,
PromptSection found``. Brain.run() bubbled it up, the channel envelope went
out without a ``reply`` field, and the bot stayed silent — the fix below
keeps the contract honest.
"""

from __future__ import annotations

from pulse.core.brain import Brain
from pulse.core.cost import CostController
from pulse.core.memory import CoreMemory, RecallMemory
from pulse.core.memory.archival_memory import ArchivalMemory
from pulse.core.task_context import TaskContext
from pulse.core.tool import ToolRegistry
from tests.pulse.support.fakes import FakeArchivalDB, FakeRecallDB


def _build_brain(tmp_path) -> Brain:  # type: ignore[no-untyped-def]
    core_memory = CoreMemory(
        storage_path=str(tmp_path / "core_memory.json"),
        soul_config_path=str(tmp_path / "soul.yaml"),
    )
    recall_memory = RecallMemory(db_engine=FakeRecallDB())
    archival_memory = ArchivalMemory(db_engine=FakeArchivalDB())
    return Brain(
        tool_registry=ToolRegistry(),
        llm_router=object(),  # type: ignore[arg-type]
        cost_controller=CostController(daily_budget_usd=5.0),
        max_steps=1,
        core_memory=core_memory,
        recall_memory=recall_memory,
        archival_memory=archival_memory,
    )


def test_render_domain_snapshot_refresh_handles_typed_prompt_sections(tmp_path) -> None:  # type: ignore[no-untyped-def]
    brain = _build_brain(tmp_path)
    brain._prompt_builder.register_domain_snapshot_provider(  # noqa: SLF001
        lambda ctx: "## Job Snapshot\n- preferred_location: ['杭州','上海']"
    )

    ctx = TaskContext(session_id="test-session", task_id="test-task")
    rendered = brain._render_domain_snapshot_refresh(ctx)  # noqa: SLF001

    assert "Memory updated after tool call" in rendered
    assert "Job Snapshot" in rendered
    assert "preferred_location" in rendered


def test_render_domain_snapshot_refresh_returns_empty_when_no_provider(tmp_path) -> None:  # type: ignore[no-untyped-def]
    brain = _build_brain(tmp_path)
    ctx = TaskContext(session_id="test-session", task_id="test-task")

    assert brain._render_domain_snapshot_refresh(ctx) == ""  # noqa: SLF001


def test_render_domain_snapshot_refresh_skips_empty_sections(tmp_path) -> None:  # type: ignore[no-untyped-def]
    brain = _build_brain(tmp_path)
    brain._prompt_builder.register_domain_snapshot_provider(lambda ctx: "")  # noqa: SLF001
    brain._prompt_builder.register_domain_snapshot_provider(lambda ctx: "   ")  # noqa: SLF001
    ctx = TaskContext(session_id="test-session", task_id="test-task")

    assert brain._render_domain_snapshot_refresh(ctx) == ""  # noqa: SLF001
