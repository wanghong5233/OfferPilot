from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.tool import tool
from ._helpers import safe_int


@tool(
    name="alarm.create",
    description="Create a local alarm reminder",
    ring="ring1_builtin",
    schema={
        "type": "object",
        "properties": {
            "minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
            "message": {"type": "string"},
        },
    },
)
def alarm_create(args: dict[str, Any]) -> dict[str, Any]:
    minutes = safe_int(args.get("minutes"), 10, min_value=1, max_value=1440)
    message = str(args.get("message") or "Reminder").strip() or "Reminder"
    now = datetime.now(timezone.utc)
    run_at = now + timedelta(minutes=minutes)
    return {
        "ok": True,
        "minutes": minutes,
        "message": message,
        "created_at": now.isoformat(),
        "run_at": run_at.isoformat(),
    }
