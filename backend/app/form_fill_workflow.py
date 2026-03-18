from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .form_autofill import fill_form_autofill_url, preview_form_autofill_url


class FormFillState(TypedDict, total=False):
    url: str
    profile: dict[str, str]
    max_actions: int
    decision: str
    feedback: str
    preview: dict[str, Any]
    fill_result: dict[str, Any]
    status: str


@dataclass
class FormFillWorkflowResult:
    thread_id: str
    status: str
    preview: dict[str, Any] | None
    fill_result: dict[str, Any] | None
    raw_state: dict[str, Any]


def _checkpoint_database_url() -> str:
    return os.getenv(
        "LANGGRAPH_CHECKPOINT_DATABASE_URL",
        os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:offerpilot@localhost:15433/offerpilot",
        ),
    )


def _safe_max_actions(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, 80))


def _preview_node(state: FormFillState) -> FormFillState:
    url = str(state.get("url") or "").strip()
    if not url:
        raise RuntimeError("Autofill preview requires non-empty URL")
    profile = state.get("profile") or {}
    preview = preview_form_autofill_url(url, profile)
    return {
        "preview": preview,
        "status": "preview_ready",
    }


def _human_review_node(state: FormFillState) -> FormFillState:
    preview = state.get("preview") if isinstance(state.get("preview"), dict) else {}
    review_payload = interrupt(
        {
            "type": "form_fill_review",
            "url": state.get("url"),
            "preview_summary": {
                "total_fields": preview.get("total_fields"),
                "mapped_fields": preview.get("mapped_fields"),
                "screenshot_path": preview.get("screenshot_path"),
            },
            "instruction": "Return decision in {approve,reject} with optional feedback and max_actions.",
        }
    )

    decision = "reject"
    feedback = ""
    max_actions = _safe_max_actions(state.get("max_actions"))
    if isinstance(review_payload, dict):
        decision = str(review_payload.get("decision") or "reject").strip().lower()
        feedback = str(review_payload.get("feedback") or "").strip()
        if "max_actions" in review_payload:
            max_actions = _safe_max_actions(review_payload.get("max_actions"))
    elif isinstance(review_payload, str):
        decision = review_payload.strip().lower()
    if decision not in {"approve", "reject"}:
        decision = "reject"

    return {
        "decision": decision,
        "feedback": feedback,
        "max_actions": max_actions,
        "status": "pending_review",
    }


def _route_after_review(state: FormFillState) -> str:
    decision = str(state.get("decision") or "reject").strip().lower()
    if decision == "approve":
        return "fill"
    return "finalize"


def _fill_node(state: FormFillState) -> FormFillState:
    url = str(state.get("url") or "").strip()
    profile = state.get("profile") or {}
    max_actions = _safe_max_actions(state.get("max_actions"))
    fill_result = fill_form_autofill_url(url, profile, max_actions=max_actions)
    return {
        "fill_result": fill_result,
        "status": "filled",
    }


def _finalize_node(state: FormFillState) -> FormFillState:
    decision = str(state.get("decision") or "reject").strip().lower()
    if decision == "approve":
        # Fill node has run before finalize on approve path.
        return {"status": "approved"}
    return {"status": "rejected"}


def _compile_form_fill_graph(checkpointer: PostgresSaver):
    graph = StateGraph(FormFillState)
    graph.add_node("preview", _preview_node)
    graph.add_node("human_review", _human_review_node)
    graph.add_node("fill", _fill_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, "preview")
    graph.add_edge("preview", "human_review")
    graph.add_conditional_edges(
        "human_review",
        _route_after_review,
        {
            "fill": "fill",
            "finalize": "finalize",
        },
    )
    graph.add_edge("fill", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _run_graph(thread_id: str, payload: dict[str, Any] | Command) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    with PostgresSaver.from_conn_string(_checkpoint_database_url()) as checkpointer:
        checkpointer.setup()
        app = _compile_form_fill_graph(checkpointer)
        result = app.invoke(payload, config=config)
    return result if isinstance(result, dict) else dict(result)


def start_form_fill_workflow(
    *,
    url: str,
    profile: dict[str, str],
    max_actions: int = 20,
) -> FormFillWorkflowResult:
    thread_id = str(uuid.uuid4())
    state = _run_graph(
        thread_id,
        {
            "url": url,
            "profile": profile,
            "max_actions": _safe_max_actions(max_actions),
            "feedback": "",
        },
    )
    interrupted = "__interrupt__" in state
    status = "pending_review" if interrupted else str(state.get("status") or "unknown")
    preview = state.get("preview") if isinstance(state.get("preview"), dict) else None
    fill_result = state.get("fill_result") if isinstance(state.get("fill_result"), dict) else None
    return FormFillWorkflowResult(
        thread_id=thread_id,
        status=status,
        preview=preview,
        fill_result=fill_result,
        raw_state=state,
    )


def resume_form_fill_workflow(
    *,
    thread_id: str,
    decision: str,
    feedback: str | None = None,
    max_actions: int | None = None,
) -> FormFillWorkflowResult:
    payload: dict[str, Any] = {"decision": decision}
    if feedback:
        payload["feedback"] = feedback
    if max_actions is not None:
        payload["max_actions"] = _safe_max_actions(max_actions)
    state = _run_graph(thread_id, Command(resume=payload))
    interrupted = "__interrupt__" in state
    status = "pending_review" if interrupted else str(state.get("status") or "unknown")
    preview = state.get("preview") if isinstance(state.get("preview"), dict) else None
    fill_result = state.get("fill_result") if isinstance(state.get("fill_result"), dict) else None
    return FormFillWorkflowResult(
        thread_id=thread_id,
        status=status,
        preview=preview,
        fill_result=fill_result,
        raw_state=state,
    )
