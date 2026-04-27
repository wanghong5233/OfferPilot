"""Game domain skill schema."""

from __future__ import annotations

from typing import Any

SKILL_SCHEMA: dict[str, Any] = {
    "name": "game",
    "description": (
        "游戏自动化域技能包：以确定性 workflow 执行低频日常任务, "
        "首发率土之滨签到 / 月卡 / 邮件 / 任务图 / 免费与半价抽卡。"
    ),
    "subcapabilities": [
        {
            "name": "workflow",
            "module": "game",
            "description": "按 game YAML 立即执行一次 dry-run 或真实任务 workflow。",
            "intents": ["game.workflow.run"],
            "examples": [
                "跑一遍率土之滨日常,先 dry run",
                "现在执行率土之滨签到和抽卡",
            ],
        },
        {
            "name": "runs",
            "module": "game",
            "description": "查看游戏自动化最近执行记录与最新结果。",
            "intents": ["game.runs.list", "game.runs.latest"],
            "examples": [
                "看一下率土之滨今天有没有跑成功",
                "列出最近几次游戏自动化记录",
            ],
        },
    ],
}
