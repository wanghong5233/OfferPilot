#!/usr/bin/env python3
"""只读测试：复现打招呼筛选链路，不执行任何点击动作。"""

from __future__ import annotations

from app.boss_scan import (
    _agent_direction_matches,
    _greet_match_threshold,
    _need_agent_direction_guard,
    _salary_matches_job_type,
    scan_boss_jobs,
)
from app.workflow import run_jd_analysis


def main() -> None:
    keyword = "大模型 Agent 实习"
    job_type = "intern"
    threshold = _greet_match_threshold()

    print(f"[STEP1] scan keyword={keyword!r}")
    items, _, _ = scan_boss_jobs(keyword, max_items=12, max_pages=1)
    print(f"  scanned={len(items)}")
    for i, item in enumerate(items):
        print(f"  - [{i}] {item.title} | salary={item.salary or 'NULL'}")

    print("\n[STEP2] salary/job_type filter")
    after_job_type = []
    for item in items:
        ok = _salary_matches_job_type(item.salary, job_type, title=item.title, snippet=item.snippet or "")
        print(f"  - {'PASS' if ok else 'DROP'} {item.title}")
        if ok:
            after_job_type.append(item)
    print(f"  remain={len(after_job_type)}")

    print("\n[STEP3] direction guard")
    after_direction = []
    if _need_agent_direction_guard(keyword):
        for item in after_job_type:
            ok, reason = _agent_direction_matches(item.title, item.snippet or "")
            print(f"  - {'PASS' if ok else 'DROP'} {item.title} | reason={reason}")
            if ok:
                after_direction.append(item)
    else:
        after_direction = after_job_type
        print("  direction guard disabled")
    print(f"  remain={len(after_direction)}")

    print(f"\n[STEP4] LLM score + should_apply (threshold={threshold})")
    matched = []
    for item in after_direction:
        jd_text = "\n".join([item.title, item.company, item.salary or "", item.snippet or ""]).strip()
        try:
            analysis = run_jd_analysis(jd_text)
            score = float(analysis.match_score)
            should_apply = bool(analysis.should_apply)
            reason = (analysis.one_line_reason or "").strip()
        except Exception as exc:
            score = 0.0
            should_apply = False
            reason = f"analysis_error={exc}"
        passed = score >= threshold and should_apply
        print(
            f"  - {'PASS' if passed else 'DROP'} {item.title} | score={score:.1f} "
            f"| should_apply={should_apply} | reason={reason}"
        )
        if passed:
            matched.append(item)

    print(f"\n[RESULT] final_matched={len(matched)}")
    for item in matched:
        print(f"  -> {item.title} | salary={item.salary or 'NULL'} | url={item.source_url or 'NULL'}")


if __name__ == "__main__":
    main()
