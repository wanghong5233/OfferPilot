from __future__ import annotations

from pulse.core.safety.context import PermissionContext
from pulse.core.safety.intent import Intent
from pulse.core.safety.policies import gacha_policy


def _ctx() -> PermissionContext:
    return PermissionContext(
        module="game",
        task_id="game:test:gacha_half",
        trace_id="trace-test",
        user_id=None,
    )


def test_gacha_policy_allows_free_pull() -> None:
    decision = gacha_policy(
        Intent(
            kind="mutation",
            name="game.test.gacha",
            args={"game_id": "test", "task_name": "gacha_free", "mode": "free"},
        ),
        _ctx(),
    )

    assert decision.kind == "allow"


def test_gacha_policy_allows_half_price_within_budget() -> None:
    decision = gacha_policy(
        Intent(
            kind="mutation",
            name="game.test.gacha",
            args={
                "game_id": "test",
                "task_name": "gacha_half",
                "mode": "half_price",
                "daily_max_pulls": 1,
                "used_today": 0,
            },
        ),
        _ctx(),
    )

    assert decision.kind == "allow"


def test_gacha_policy_asks_when_half_price_budget_exceeded() -> None:
    decision = gacha_policy(
        Intent(
            kind="mutation",
            name="game.test.gacha",
            args={
                "game_id": "test",
                "task_name": "gacha_half",
                "mode": "half_price",
                "daily_max_pulls": 1,
                "used_today": 1,
            },
        ),
        _ctx(),
    )

    assert decision.kind == "ask"
    assert decision.ask_request is not None
    assert decision.ask_request.resume_handle.module == "game"
