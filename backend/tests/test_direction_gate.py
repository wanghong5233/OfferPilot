#!/usr/bin/env python3
"""只读方向门控测试：不打招呼，不点击按钮。"""

from app.boss_scan import _agent_direction_matches


def main() -> None:
    samples = [
        (
            "大模型算法实习生",
            "大模型基座研发与优化，参与 Pre-training / Post-train，持续算法创新",
        ),
        (
            "AI Agent开发实习生",
            "负责 Agent 工作流、RAG、LangGraph、MCP 工程化落地",
        ),
        (
            "大模型应用工程师（Agent方向）",
            "围绕业务场景做 LLM 应用开发与智能体系统搭建",
        ),
    ]
    for title, snippet in samples:
        ok, reason = _agent_direction_matches(title, snippet)
        print(f"{title} -> pass={ok}, reason={reason}")


if __name__ == "__main__":
    main()
