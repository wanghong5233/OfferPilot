from __future__ import annotations

import os
import urllib.parse
from typing import Any

from ..core.tool import tool
from ._helpers import http_get_json, safe_float, safe_int


@tool(
    name="flight.search",
    description="Search flights via configured external provider",
    ring="ring1_builtin",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        },
    },
)
def flight_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip() or "Beijing -> Shanghai"
    max_results = safe_int(args.get("max_results"), 3, min_value=1, max_value=8)
    base_url = str(os.getenv("PULSE_FLIGHT_SEARCH_BASE_URL", "")).strip().rstrip("/")
    auth_token = str(os.getenv("PULSE_FLIGHT_SEARCH_TOKEN", "")).strip()
    timeout_sec = safe_float(
        os.getenv("PULSE_FLIGHT_SEARCH_TIMEOUT_SEC", "8"), 8.0, min_value=2.0, max_value=20.0,
    )
    if not base_url:
        return {
            "ok": False, "query": query, "total": 0, "items": [],
            "source": "external_api",
            "error": "PULSE_FLIGHT_SEARCH_BASE_URL is not configured",
        }
    params = urllib.parse.urlencode({"query": query, "max_results": str(max_results)})
    url = f"{base_url}?{params}" if "?" not in base_url else f"{base_url}&{params}"
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    try:
        payload = http_get_json(url, timeout_sec=timeout_sec, headers=headers)
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items_raw = payload.get("items")
            items = items_raw if isinstance(items_raw, list) else []
        else:
            items = []
        safe_items = [item for item in items if isinstance(item, dict)][:max_results]
        return {
            "ok": True, "query": query, "total": len(safe_items),
            "items": safe_items, "source": "external_api",
        }
    except Exception as exc:
        return {
            "ok": False, "query": query, "total": 0, "items": [],
            "source": "external_api", "error": str(exc)[:300],
        }
