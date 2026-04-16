from __future__ import annotations

from typing import Any

from ..core.tool import tool
from ..core.tools.web_search import search_web
from ._helpers import safe_int


@tool(
    name="web.search",
    description="Search web and return top results",
    ring="ring1_builtin",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 12},
        },
    },
)
def web_search_tool(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    max_results = safe_int(args.get("max_results"), 5, min_value=1, max_value=12)
    rows = search_web(query, max_results=max_results)
    return {
        "query": query,
        "total": len(rows),
        "items": [
            {"title": item.title, "url": item.url, "snippet": item.snippet}
            for item in rows
        ],
    }
