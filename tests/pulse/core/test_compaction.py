from __future__ import annotations

from pulse.core.compaction import (
    CompactionInput,
    CompactionLevel,
    RuleCompactionStrategy,
)
from pulse.core.task_context import TaskContext
from pulse.core.tokenizer import count_tokens


def test_rule_compaction_uses_token_budgeted_preview() -> None:
    strategy = RuleCompactionStrategy(max_obs_tokens=40, max_answer_tokens=40)
    output = strategy.compact(
        CompactionInput(
            ctx=TaskContext(),
            level=CompactionLevel.turn_to_taskrun,
            raw_steps=[
                {
                    "tool_name": "job.greet.scan",
                    "observation": {"items": ["岗位描述" * 100]},
                },
            ],
        )
    )

    assert "job.greet.scan" in output.summary
    assert "preview truncated to 40 tokens" in output.summary
    assert output.token_estimate == count_tokens(output.summary)

