#!/usr/bin/env python3
"""只读测试：新漏斗架构全链路验证。
搜索 → 薪资过滤 → 方向门控 → 详情页JD提取 → LLM二元判断。
不执行任何点击/打招呼动作。
"""
from __future__ import annotations

import random

from app.boss_scan import (
    _agent_direction_matches,
    _extract_detail_text,
    _get_browser_context,
    _get_page,
    _need_agent_direction_guard,
    _salary_matches_job_type,
    scan_boss_jobs,
)
from app.workflow import run_greet_decision


def main() -> None:
    keyword = "大模型 Agent 实习"
    job_type = "intern"

    print(f"{'='*60}")
    print(f"[STEP1] 搜索 keyword={keyword!r}")
    print(f"{'='*60}")
    items, _, _ = scan_boss_jobs(keyword, max_items=10, max_pages=1)
    print(f"  扫描到 {len(items)} 个岗位")
    for i, item in enumerate(items):
        print(f"  [{i}] {item.title} | salary={item.salary or 'NULL'} | url={item.source_url or 'NULL'}")

    print(f"\n{'='*60}")
    print(f"[STEP2] 薪资/job_type 过滤")
    print(f"{'='*60}")
    after_salary = []
    for item in items:
        ok = _salary_matches_job_type(item.salary, job_type, title=item.title, snippet=item.snippet or "")
        tag = "PASS" if ok else "DROP"
        print(f"  [{tag}] {item.title} | salary={item.salary or 'NULL'}")
        if ok:
            after_salary.append(item)
    print(f"  剩余: {len(after_salary)}")

    print(f"\n{'='*60}")
    print(f"[STEP3] 方向门控")
    print(f"{'='*60}")
    after_direction = []
    if _need_agent_direction_guard(keyword):
        for item in after_salary:
            ok, reason = _agent_direction_matches(item.title, item.snippet or "")
            tag = "PASS" if ok else "DROP"
            print(f"  [{tag}] {item.title} | reason={reason}")
            if ok:
                after_direction.append(item)
    else:
        after_direction = after_salary
        print("  方向门控未启用")
    print(f"  剩余: {len(after_direction)}")

    print(f"\n{'='*60}")
    print(f"[STEP4] 详情页JD提取 + LLM二元判断")
    print(f"{'='*60}")

    context = _get_browser_context()
    page = _get_page(context)

    final_pass = []
    for idx, item in enumerate(after_direction):
        if not item.source_url:
            print(f"  [{idx}] SKIP (无URL): {item.title}")
            continue

        print(f"\n  --- [{idx}] {item.title}@{item.company} ---")
        print(f"  URL: {item.source_url}")

        try:
            page.goto(item.source_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(random.randint(1500, 2500))
        except Exception as exc:
            print(f"  [ERROR] 导航失败: {exc}")
            continue

        full_jd = _extract_detail_text(page)
        jd_len = len(full_jd.strip()) if full_jd else 0
        print(f"  JD提取: {jd_len} 字")
        if jd_len > 0:
            preview = full_jd.strip()[:200].replace("\n", " | ")
            print(f"  JD预览: {preview}...")
        else:
            print(f"  [WARNING] JD提取失败！使用snippet替代")
            full_jd = item.snippet or ""

        jd_context = f"岗位标题：{item.title}\n公司：{item.company}\n薪资：{item.salary or '未知'}\n\n职位描述：\n{full_jd}"

        decision = run_greet_decision(jd_context)
        tag = "PASS" if decision.should_greet else "REJECT"
        print(f"  LLM判断: [{tag}] reason={decision.reason} | confidence={decision.confidence}")

        if decision.should_greet:
            final_pass.append(item)

    print(f"\n{'='*60}")
    print(f"[RESULT] 最终通过: {len(final_pass)}")
    print(f"{'='*60}")
    for item in final_pass:
        print(f"  -> {item.title}@{item.company} | salary={item.salary or 'NULL'}")


if __name__ == "__main__":
    main()
