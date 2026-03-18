from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .agent_events import EventType, emit
from .boss_scan import scan_boss_jobs
from .schemas import BossScanItem, BossScanResponse
from .storage import log_action, persist_jd_analysis
from .vector_store import upsert_jd_history
from .workflow import run_jd_analysis


class BossScanState(TypedDict, total=False):
    keyword: str
    max_items: int
    max_pages: int
    pages_scanned: int
    screenshot_path: str | None
    raw_items: list[dict[str, Any]]
    items: list[dict[str, Any]]


def _scan_node(state: BossScanState) -> BossScanState:
    emit(EventType.WORKFLOW_NODE, "scan: 执行BOSS岗位扫描")
    keyword = str(state.get("keyword") or "").strip()
    max_items = int(state.get("max_items") or 10)
    max_pages = int(state.get("max_pages") or 1)
    items, screenshot_path, pages_scanned = scan_boss_jobs(
        keyword,
        max_items=max_items,
        max_pages=max_pages,
    )
    return {
        "screenshot_path": screenshot_path,
        "pages_scanned": pages_scanned,
        "raw_items": [item.model_dump() for item in items],
    }


def _analyze_and_persist_node(state: BossScanState) -> BossScanState:
    emit(EventType.WORKFLOW_NODE, "analyze_persist: JD分析 + 持久化")
    keyword = str(state.get("keyword") or "")
    raw_items = state.get("raw_items") or []
    enriched: list[dict[str, Any]] = []

    for raw in raw_items:
        try:
            item = BossScanItem.model_validate(raw)
        except Exception:
            continue

        jd_text = "\n".join([item.title, item.company, item.snippet or ""]).strip()
        try:
            analysis = run_jd_analysis(jd_text)
            item = item.model_copy(update={"match_score": analysis.match_score})
            job_id = persist_jd_analysis(
                jd_text,
                analysis,
                source="boss_scan",
                source_url=item.source_url,
            )
            if job_id:
                upsert_jd_history(
                    doc_id=job_id,
                    jd_text=jd_text,
                    title=analysis.title,
                    company=analysis.company,
                    match_score=analysis.match_score,
                )
                log_action(
                    job_id=job_id,
                    action_type="boss_scan",
                    input_summary=f"keyword={keyword}",
                    output_summary=f"title={item.title}; company={item.company}; score={item.match_score}",
                    status="success",
                )
        except Exception:
            # Keep workflow robust: return raw scan result even if analysis fails.
            pass
        enriched.append(item.model_dump())

    return {"items": enriched}


def _build_graph():
    graph = StateGraph(BossScanState)
    graph.add_node("scan", _scan_node)
    graph.add_node("analyze_persist", _analyze_and_persist_node)
    graph.add_edge(START, "scan")
    graph.add_edge("scan", "analyze_persist")
    graph.add_edge("analyze_persist", END)
    return graph.compile()


_GRAPH = _build_graph()


def run_boss_scan_workflow(keyword: str, max_items: int, max_pages: int = 1) -> BossScanResponse:
    emit(EventType.WORKFLOW_START, f"boss_scan_workflow: keyword={keyword}")
    state = _GRAPH.invoke({"keyword": keyword, "max_items": max_items, "max_pages": max_pages})
    items_raw = state.get("items") or []
    items: list[BossScanItem] = []
    for raw in items_raw:
        try:
            items.append(BossScanItem.model_validate(raw))
        except Exception:
            continue
    return BossScanResponse(
        keyword=keyword,
        total=len(items),
        pages_scanned=int(state.get("pages_scanned") or 1),
        screenshot_path=state.get("screenshot_path"),
        items=items,
    )
