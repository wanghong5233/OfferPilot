from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .material_service import generate_material_draft
from .schemas import MaterialDraft


class MaterialState(TypedDict, total=False):
    job_id: str
    title: str
    company: str
    match_score: float | None
    skills: list[str]
    jd_raw: str
    resume_version: str
    feedback: str
    draft: dict[str, Any]
    decision: str
    status: str


@dataclass
class MaterialWorkflowResult:
    thread_id: str
    status: str
    draft: MaterialDraft | None
    raw_state: dict[str, Any]


def _checkpoint_database_url() -> str:
    return os.getenv(
        "LANGGRAPH_CHECKPOINT_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:offerpilot@localhost:15433/offerpilot",
        ),
    )


def _generator_node(state: MaterialState) -> MaterialState:
    draft = generate_material_draft(
        title=state.get("title", "Unknown Title"),
        company=state.get("company", "Unknown Company"),
        skills=state.get("skills", []),
        jd_raw=state.get("jd_raw", ""),
        resume_version=state.get("resume_version", "resume_v1"),
        feedback=state.get("feedback"),
    )
    return {
        "draft": draft.model_dump(),
        "status": "generated",
    }


def _human_review_node(state: MaterialState) -> MaterialState:
    review_payload = interrupt(
        {
            "type": "material_review",
            "title": state.get("title"),
            "company": state.get("company"),
            "draft": state.get("draft"),
            "instruction": "Return decision in {approve,reject,regenerate} with optional feedback.",
        }
    )
    decision = "reject"
    feedback = ""
    if isinstance(review_payload, dict):
        decision = str(review_payload.get("decision") or "reject").strip().lower()
        feedback = str(review_payload.get("feedback") or "").strip()
    elif isinstance(review_payload, str):
        decision = review_payload.strip().lower()
    if decision not in {"approve", "reject", "regenerate"}:
        decision = "reject"
    return {
        "decision": decision,
        "feedback": feedback,
        "status": "pending_review",
    }


def _route_after_review(state: MaterialState) -> str:
    decision = str(state.get("decision") or "reject").strip().lower()
    if decision == "regenerate":
        return "generator"
    return "finalize"


def _finalize_node(state: MaterialState) -> MaterialState:
    decision = str(state.get("decision") or "reject").strip().lower()
    if decision == "approve":
        return {"status": "approved"}
    if decision == "reject":
        return {"status": "rejected"}
    return {"status": "rejected"}


def _compile_material_graph(checkpointer: PostgresSaver):
    graph = StateGraph(MaterialState)
    graph.add_node("generator", _generator_node)
    graph.add_node("human_review", _human_review_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, "generator")
    graph.add_edge("generator", "human_review")
    graph.add_conditional_edges(
        "human_review",
        _route_after_review,
        {
            "generator": "generator",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _state_to_draft(state: dict[str, Any]) -> MaterialDraft | None:
    raw = state.get("draft")
    if not isinstance(raw, dict):
        return None
    try:
        return MaterialDraft.model_validate(raw)
    except Exception:
        return None


def _run_graph(thread_id: str, payload: dict[str, Any] | Command) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    with PostgresSaver.from_conn_string(_checkpoint_database_url()) as checkpointer:
        checkpointer.setup()
        app = _compile_material_graph(checkpointer)
        result = app.invoke(payload, config=config)
    return result if isinstance(result, dict) else dict(result)


def start_material_workflow(
    *,
    job_id: str,
    title: str,
    company: str,
    match_score: float | None,
    skills: list[str],
    jd_raw: str,
    resume_version: str,
) -> MaterialWorkflowResult:
    thread_id = str(uuid.uuid4())
    state = _run_graph(
        thread_id,
        {
            "job_id": job_id,
            "title": title,
            "company": company,
            "match_score": match_score,
            "skills": skills,
            "jd_raw": jd_raw,
            "resume_version": resume_version,
            "feedback": "",
        },
    )
    interrupted = "__interrupt__" in state
    status = "pending_review" if interrupted else str(state.get("status") or "unknown")
    return MaterialWorkflowResult(
        thread_id=thread_id,
        status=status,
        draft=_state_to_draft(state),
        raw_state=state,
    )


def resume_material_workflow(
    *,
    thread_id: str,
    decision: str,
    feedback: str | None = None,
) -> MaterialWorkflowResult:
    payload = {"decision": decision}
    if feedback:
        payload["feedback"] = feedback
    state = _run_graph(thread_id, Command(resume=payload))
    interrupted = "__interrupt__" in state
    status = "pending_review" if interrupted else str(state.get("status") or "unknown")
    return MaterialWorkflowResult(
        thread_id=thread_id,
        status=status,
        draft=_state_to_draft(state),
        raw_state=state,
    )
