"""Prompt budget allocator unit tests.

Locks in the contract that replaced the historical hardcoded
``prompt[:6000]`` truncation in ``Brain._build_system_prompt``:

  * Total prompt token count under budget → all sections kept.
  * Total over budget → drop highest-priority-number sections first
    (P3 archival/recall before P2 recent), biggest section first within a tier.
  * Even after dropping every P2/P3 section, P0/P1 still exceeds the budget
    → ``RuntimeError`` (fail-loud, §1.1) rather than silent char-cut.
  * The budget operates on real tokens, not chars — CJK content gets
    counted ~1:1, ASCII ~1:4, via ``count_tokens``.
"""

from __future__ import annotations

from typing import Any

import pytest

from pulse.core.prompt_contract import (
    PromptContract,
    PromptContractBuilder,
    PromptSection,
    SectionPriority,
)
from pulse.core.task_context import create_interactive_context
from pulse.core.tokenizer import count_tokens
from pulse.core.tool import ToolSpec


class _FakeMemory:
    """Minimal MemoryReader with knobs for stuffing huge sections."""

    def __init__(
        self,
        *,
        soul: dict[str, Any] | None = None,
        user: dict[str, Any] | None = None,
        prefs: dict[str, Any] | None = None,
        recent: list[dict[str, Any]] | None = None,
        recall_hits: list[dict[str, Any]] | None = None,
        archival: list[dict[str, Any]] | None = None,
    ) -> None:
        self._snapshot = {
            "soul": soul or {},
            "user": user or {},
            "prefs": prefs or {},
        }
        self._recent = list(recent or [])
        self._recall = list(recall_hits or [])
        self._archival = list(archival or [])

    def read_core_snapshot(self) -> dict[str, Any]:
        return dict(self._snapshot)

    def read_recent(self, session_id: str | None, limit: int) -> list[dict[str, Any]]:
        return list(self._recent)

    def search_recall(
        self, query: str, session_id: str | None, top_k: int
    ) -> list[dict[str, Any]]:
        return list(self._recall)

    def search_archival(self, query: str, limit: int) -> list[dict[str, Any]]:
        return list(self._archival)

    def read_workspace_essentials(self, workspace_id: str | None) -> dict[str, Any]:
        return {}


def _heavy_text(token_target: int) -> str:
    """Generate ~``token_target`` tokens of CJK content for budget tests.

    CJK is ~1 char/token under tiktoken's o200k_base, so target ≈ chars.
    Actual count varies — tests only assert relative ordering, not exact
    tokens, so ±10% drift is fine.
    """
    return "中文内容用来填充上下文以测试预算治理" * (token_target // 18 + 1)


def _builder(**kwargs: Any) -> PromptContractBuilder:
    return PromptContractBuilder(**kwargs)


def test_budget_keeps_all_sections_when_under_limit() -> None:
    memory = _FakeMemory(
        soul={"assistant_prefix": "Pulse", "role": "test"},
        recent=[{"role": "user", "text": "hi"}],
        recall_hits=[{"text": "earlier chat", "similarity": 0.5}],
        archival=[{"subject": "X", "predicate": "is", "object": "Y"}],
    )
    builder = _builder(
        memory=memory,
        tool_specs=[ToolSpec(name="t", description="d", when_to_use="w", when_not_to_use="n")],
        max_input_tokens=100_000,
    )
    contract = builder.build(create_interactive_context(session_id="s"), "q")
    assert contract.dropped_sections == ()
    assert "Pulse" in contract.text
    assert "earlier chat" in contract.text
    assert "X is Y" in contract.text


def test_budget_drops_archival_before_recall_before_recent() -> None:
    """When the budget is just over, P3 (archival/recall) drops before P2 (recent).

    Empirical baseline (audit 2026-04): default ``_build_system`` produces
    ~1300 tokens of P0 frame (identity + tools + tool_use_policy +
    command_conventions + output_contract) before any memory contribution.
    So budget tests must allow a *few hundred* tokens above 1300 of memory
    headroom to exercise the drop path without false failing on the frame.
    """
    big_recall = [{"text": _heavy_text(400), "similarity": 0.9}]
    big_archival = [
        {"subject": _heavy_text(400), "predicate": "is", "object": "X"}
    ]
    big_recent = [{"role": "user", "text": _heavy_text(200)}]
    memory = _FakeMemory(
        recent=big_recent,
        recall_hits=big_recall,
        archival=big_archival,
    )
    # ~1500 tokens lets P0 frame (1300) + recent (~250) survive while
    # forcing archival + recall (~800 combined) to drop.
    builder = _builder(
        memory=memory,
        tool_specs=[ToolSpec(name="t", description="d")],
        max_input_tokens=1500,
    )
    contract = builder.build(create_interactive_context(session_id="s"), "q")
    assert "archival" in contract.dropped_sections
    assert "recall" in contract.dropped_sections
    assert "## Recent Conversation History" in contract.text


def test_budget_fails_loud_when_p0_p1_alone_exceed_budget() -> None:
    """If the prompt blows the budget even with everything droppable gone,
    raise — silent truncation would let the model reason with corrupt context.
    """
    huge_user_prefs = {"key": _heavy_text(5000)}
    memory = _FakeMemory(prefs=huge_user_prefs)
    builder = _builder(
        memory=memory,
        tool_specs=[ToolSpec(name="t", description="d")],
        max_input_tokens=200,
    )
    with pytest.raises(RuntimeError, match="prompt budget exhausted"):
        builder.build(create_interactive_context(session_id="s"), "q")


def test_token_estimate_uses_real_tokenizer_not_char_div_three() -> None:
    """Sanity check: char-based old impl gave ``len // 3``; real tokenizer
    will deviate, especially for CJK. The contract's ``token_estimate``
    must match ``count_tokens`` over the kept sections, not ``len // 3``.
    """
    memory = _FakeMemory(soul={"assistant_prefix": "Pulse"})
    builder = _builder(memory=memory, tool_specs=[ToolSpec(name="t", description="d")])
    contract: PromptContract = builder.build(create_interactive_context(session_id="s"), "q")
    expected = sum(count_tokens(s) for s in contract.sections)
    assert contract.token_estimate == expected


def test_budget_drops_largest_within_same_priority_tier() -> None:
    """Within priority P3 (recall + archival both = 3), the *bigger* one
    drops first — biggest savings per drop.
    """
    builder = _builder(max_input_tokens=10_000)
    sections = [
        PromptSection(name="identity", text="ID", priority=SectionPriority.IDENTITY),
        PromptSection(name="recall_small", text="abc", priority=SectionPriority.RECALL),
        PromptSection(name="archival_huge", text=_heavy_text(20_000), priority=SectionPriority.ARCHIVAL),
    ]
    kept, dropped, _ = builder._allocate_budget(sections, contract_type=None)  # type: ignore[arg-type]
    assert "archival_huge" in dropped
    assert any(s.name == "identity" for s in kept)
