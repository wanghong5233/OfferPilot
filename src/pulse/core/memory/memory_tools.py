from __future__ import annotations

from typing import Any

from ..tool import ToolRegistry, tool
from .archival_memory import ArchivalMemory
from .core_memory import CoreMemory
from .recall_memory import RecallMemory


def register_memory_tools(
    registry: ToolRegistry,
    *,
    core_memory: CoreMemory,
    recall_memory: RecallMemory,
    archival_memory: ArchivalMemory | None = None,
) -> None:
    @tool(
        name="memory_read",
        description="Read core memory block or full snapshot",
        schema={
            "type": "object",
            "properties": {
                "block": {"type": "string", "description": "One of: soul, user, prefs, context. Omit for full snapshot."},
            },
        },
    )
    def _memory_read(args: dict[str, Any]) -> dict[str, Any]:
        block = str(args.get("block") or "").strip().lower()
        if block:
            return {
                "block": block,
                "value": core_memory.read_block(block),
            }
        return {"snapshot": core_memory.snapshot()}

    @tool(
        name="memory_update",
        description="Update core memory block or preferences",
        schema={
            "type": "object",
            "properties": {
                "block": {"type": "string", "description": "Block name: soul, user, prefs, context"},
                "content": {"description": "Content to update (dict for prefs)"},
                "merge": {"type": "boolean", "description": "Whether to merge with existing (default true)"},
            },
        },
    )
    def _memory_update(args: dict[str, Any]) -> dict[str, Any]:
        block = str(args.get("block") or "prefs").strip().lower()
        merge = bool(args.get("merge", True))
        content = args.get("content")
        if block == "prefs":
            if not isinstance(content, dict):
                raise ValueError("prefs update requires dict content")
            updated = core_memory.update_preferences(content)
            return {"block": "prefs", "updated": updated}
        updated = core_memory.update_block(block=block, content=content, merge=merge)
        return {"block": block, "updated": updated}

    @tool(
        name="memory_search",
        description="Search recall memory (conversation history) semantically",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "description": "Max results (default 5)"},
                "session_id": {"type": "string", "description": "Optional session filter"},
            },
        },
    )
    def _memory_search(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        top_k_raw = args.get("top_k", 5)
        try:
            top_k = int(top_k_raw)
        except Exception:
            top_k = 5
        session_id = str(args.get("session_id") or "").strip() or None
        rows = recall_memory.search(query=query, top_k=top_k, session_id=session_id)
        return {"query": query, "total": len(rows), "items": rows}

    @tool(
        name="memory_archive",
        description="Store an important fact in long-term archival memory (subject-predicate-object triple)",
        schema={
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity or topic (e.g. 'user', 'project_pulse')"},
                "predicate": {"type": "string", "description": "Relationship or attribute (e.g. 'prefers', 'lives_in')"},
                "object": {"type": "string", "description": "Value or target (e.g. 'remote work', 'Beijing')"},
                "confidence": {"type": "number", "description": "Confidence 0-1 (default 1.0)"},
                "source": {"type": "string", "description": "Source of this fact (e.g. 'user_statement', 'inferred')"},
            },
            "required": ["subject", "predicate", "object"],
        },
    )
    def _memory_archive(args: dict[str, Any]) -> dict[str, Any]:
        if archival_memory is None:
            return {"ok": False, "error": "archival memory not available"}
        subject = str(args.get("subject") or "").strip()
        predicate = str(args.get("predicate") or "").strip()
        obj = str(args.get("object") or "").strip()
        if not subject or not predicate or not obj:
            raise ValueError("subject, predicate, and object are all required")
        confidence = max(0.0, min(float(args.get("confidence") or 1.0), 1.0))
        source = str(args.get("source") or "brain").strip()
        result = archival_memory.add_fact(
            subject=subject,
            predicate=predicate,
            object_value=obj,
            confidence=confidence,
            source=source,
        )
        return {"ok": True, "fact": result}

    registry.register_callable(_memory_read)
    registry.register_callable(_memory_update)
    registry.register_callable(_memory_search)
    registry.register_callable(_memory_archive)
