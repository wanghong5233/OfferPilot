"""Tokenizer abstraction for prompt budget accounting.

Industry rule (cf. Claude Code / Aider / OpenAI cookbook): never use
``len(text) // N`` to estimate token cost. The drift between char count
and real tokenizer output is large (CJK ~ 1 char/token, ASCII ~ 4
chars/token, code/JSON ~ 3 chars/token) and silent over-stuffing is the
single most common cause of "context-truncated → model went dumb"
production incidents.

Strategy:
  * For OpenAI-family models (gpt-/o1-/o3-/o4-) use ``tiktoken``'s exact
    BPE encoder. Tiktoken is already a transitive dep via the openai SDK.
  * For non-OpenAI models (qwen, claude-* via OpenAI compatible gateways,
    custom endpoints) fall back to a CJK-aware heuristic that empirically
    tracks the qwen / Claude tokenizers within ~10 percent.
  * The heuristic intentionally over-estimates by 5 percent — staying
    *under* the model's window is always safer than blowing it.

Calls to ``count_tokens`` are best-effort: they never raise, and on any
internal failure they fall back to the heuristic. A failure to count
should not fail a request.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# Tiktoken can resolve encodings directly only for OpenAI-family names.
# Everything else takes the heuristic path.
_OPENAI_FAMILY = re.compile(r"^(gpt-|o1-|o3-|o4-|chatgpt-)", re.IGNORECASE)

# CJK Unified Ideographs + CJK punctuation + full-width forms.
# A reasonable proxy for "characters that map ~1:1 to a token".
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


@lru_cache(maxsize=8)
def _get_encoding(model: str) -> Any | None:
    """Resolve a tiktoken encoder for *model* or ``None`` for heuristic.

    Uses ``encoding_for_model`` for OpenAI-family names, falls back to
    ``o200k_base`` (gpt-4o family BPE) for everything else where tiktoken
    is still useful as a rough estimator. Returns ``None`` if tiktoken
    is not installed at all so the caller can switch to the heuristic.
    """
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        if _OPENAI_FAMILY.match(model):
            return tiktoken.encoding_for_model(model)
    except KeyError:
        pass
    try:
        return tiktoken.get_encoding("o200k_base")
    except (KeyError, ValueError) as exc:
        logger.warning("tokenizer: tiktoken encoding load failed: %s", exc)
        return None


def _heuristic_count(text: str) -> int:
    """CJK char ~ 1 token, ASCII ~ 4 chars/token, plus 5 percent safety."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    ascii_chars = len(text) - cjk
    base = cjk + max(1, ascii_chars // 4)
    return int(base * 1.05) + 1


def count_tokens(text: str, *, model: str = "gpt-4o-mini") -> int:
    """Return real token count under *model*'s BPE, with fallback heuristic.

    Never raises. Empty input returns 0. Used by the prompt budget allocator
    to decide which sections to drop, by ``CostController.estimate_tokens``
    to attribute USD spend, and by tests asserting prompt size invariants.
    """
    if not text:
        return 0
    enc = _get_encoding(model)
    if enc is None:
        return _heuristic_count(text)
    try:
        return len(enc.encode(text))
    except (TypeError, ValueError) as exc:
        logger.debug("tokenizer: encode failed (%s); falling back: %s", model, exc)
        return _heuristic_count(text)


def token_preview(
    text: str,
    *,
    max_tokens: int,
    model: str = "gpt-4o-mini",
    suffix: str | None = None,
) -> str:
    """Return a token-budgeted head+tail preview without char magic.

    Use this only when a bounded preview is the product contract (prompt
    snippets, audit previews, rule compaction breadcrumbs). Do not use it to
    hide required context; prompt-level completeness should be handled by
    section budgeting instead.

    The preview preserves both the beginning and the end. Job/email texts often
    place role overview at the front but salary/location/deadline at the end;
    prefix-only truncation loses exactly the facts the matcher needs.
    """
    raw = str(text or "")
    if not raw:
        return ""
    safe_max = max(1, int(max_tokens))
    marker = suffix if suffix is not None else f"... [preview truncated to {safe_max} tokens]"
    if count_tokens(raw, model=model) <= safe_max:
        return raw
    lo, hi = 0, len(raw)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        head_len = mid // 2
        tail_len = mid - head_len
        candidate = raw[:head_len] + marker + raw[-tail_len:]
        if count_tokens(candidate, model=model) <= safe_max:
            lo = mid
        else:
            hi = mid - 1
    head_len = lo // 2
    tail_len = lo - head_len
    return raw[:head_len] + marker + raw[-tail_len:]


# Conservative input budgets per model (context window minus a generous
# completion reserve). Used as the ``max_input_tokens`` default for the
# prompt builder when the caller has not specified one.
#
# Sources: OpenAI / Alibaba / Anthropic public model cards as of 2026-04.
# Numbers are conservative on purpose — better to drop low-priority
# sections one round earlier than to bump the model's hard cap.
_MODEL_INPUT_BUDGETS: dict[str, int] = {
    "gpt-4.1":           120_000,   # 1M total - completion reserve
    "gpt-4.1-mini":      120_000,
    "gpt-4o":            120_000,   # 128k total
    "gpt-4o-mini":       120_000,
    "gpt-4-turbo":       120_000,
    "o1-preview":         24_000,
    "o3-mini":           120_000,
    "o4-mini":           120_000,
    "qwen-max-latest":     28_000,  # 32k total
    "qwen-plus-latest":   120_000,  # 131k total
    "qwen-turbo-latest":  120_000,
    "qwen-vl-max-latest":  28_000,
    "claude-3-5-sonnet": 180_000,
    "claude-3-5-haiku":  180_000,
}

# Floor used when the resolved model is unknown — equals the smallest
# fallback in our router (qwen-max-latest 32k - 4k reserve).
DEFAULT_INPUT_BUDGET = 24_000


def model_input_budget(model: str) -> int:
    """Return a conservative *input* token budget for *model*.

    Falls back to ``DEFAULT_INPUT_BUDGET`` (qwen-max worst-case minus
    completion reserve) for unknown names so calling code stays correct
    when a new model is wired up before this map is updated.
    """
    if not model:
        return DEFAULT_INPUT_BUDGET
    return _MODEL_INPUT_BUDGETS.get(model.strip(), DEFAULT_INPUT_BUDGET)
