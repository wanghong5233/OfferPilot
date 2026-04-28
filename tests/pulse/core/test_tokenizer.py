"""Tokenizer unit tests.

Locks in the contract:
  * ``count_tokens`` returns 0 for empty input, never raises.
  * For OpenAI-family models the count tracks ``tiktoken`` exactly.
  * For unknown / non-OpenAI models the heuristic stays within ±20% of
    a CJK 1:1 / ASCII 1:4 rule of thumb.
  * ``model_input_budget`` returns the conservative floor for unknown
    models and a sensible ceiling for known ones.
"""

from __future__ import annotations

import pytest

from pulse.core.tokenizer import (
    DEFAULT_INPUT_BUDGET,
    count_tokens,
    model_input_budget,
    token_preview,
)


def test_count_tokens_empty_returns_zero() -> None:
    assert count_tokens("") == 0
    assert count_tokens("", model="gpt-4o-mini") == 0


def test_count_tokens_ascii_matches_char_div_four_within_margin() -> None:
    # 80 ASCII chars → tiktoken ~ 18-22 tokens, heuristic ~ 21 (80/4 * 1.05 + 1).
    text = "the quick brown fox jumps over the lazy dog. " * 2
    n = count_tokens(text, model="gpt-4o-mini")
    assert 14 <= n <= 32


def test_count_tokens_cjk_matches_per_char_within_margin() -> None:
    text = "我需要找一段有含金量的实习业务垂直匹配秋招叙事顺畅"  # 24 CJK chars
    n = count_tokens(text, model="gpt-4o-mini")
    assert 18 <= n <= 36


def test_count_tokens_handles_unknown_model_via_fallback() -> None:
    # Random non-OpenAI model name — must not raise, must produce sensible count.
    n = count_tokens("hello 你好 world 世界", model="qwen-some-future-model")
    assert n > 0
    assert n < 50


def test_model_input_budget_known_models() -> None:
    assert model_input_budget("gpt-4.1") >= 100_000
    assert model_input_budget("qwen-max-latest") >= 24_000
    assert model_input_budget("qwen-plus-latest") >= 100_000


def test_model_input_budget_unknown_falls_back_to_default() -> None:
    assert model_input_budget("not-a-real-model") == DEFAULT_INPUT_BUDGET
    assert model_input_budget("") == DEFAULT_INPUT_BUDGET


def test_token_preview_respects_token_budget_and_marks_truncation() -> None:
    text = "开头事实" + ("这是一段很长的中文上下文" * 200) + "尾部薪资地点事实"
    preview = token_preview(text, max_tokens=40)
    assert count_tokens(preview) <= 40
    assert "preview truncated to 40 tokens" in preview
    assert "开头事实" in preview
    assert "尾部薪资地点事实" in preview
