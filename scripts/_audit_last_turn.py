"""Render a compact timeline for the most recent dispatch trace.

Reads ``data/exports/events/pulse_events-<today>.jsonl``, picks the latest
``trace_id`` (whichever one was most recently used by ``brain.run.started``
or ``channel.message.routed``), and prints every event under that trace
chronologically. Pure read-only audit helper.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def _today_path() -> Path:
    today = dt.date.today().strftime("%Y%m%d")
    return Path("data/exports/events") / f"pulse_events-{today}.jsonl"


def _iter_events(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _format(evt: dict) -> str:
    ts = str(evt.get("timestamp", ""))[:19]
    et = evt.get("event_type", "")
    tr = str(evt.get("trace_id", ""))[-12:]
    ac = evt.get("actor", "")
    parts = [ts, et, tr, ac]
    keys = (
        "name", "patrol_name", "tool_name", "enabled", "ok",
        "stopped_reason", "error", "field", "value", "domain", "op", "status",
        "trigger_now", "summary",
    )
    extras = {}
    for key in keys:
        if key in evt:
            extras[key] = evt[key]
    payload = evt.get("payload")
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and key not in extras:
                extras[key] = payload[key]
    if extras:
        parts.append(json.dumps(extras, ensure_ascii=False))
    return " | ".join(str(p) for p in parts if p)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", help="Specific trace_id to filter (default: most recent)")
    parser.add_argument("--path", help="Override jsonl path")
    parser.add_argument("--all-traces", action="store_true", help="Print all events")
    args = parser.parse_args()

    path = Path(args.path) if args.path else _today_path()
    if not path.exists():
        print(f"event log not found: {path}", file=sys.stderr)
        return 2

    events = list(_iter_events(path))
    if not events:
        print("no events", file=sys.stderr)
        return 2

    target = args.trace
    if not target and not args.all_traces:
        for evt in reversed(events):
            if evt.get("event_type") in {"brain.run.started", "channel.message.routed"}:
                target = str(evt.get("trace_id") or "")
                break
    if args.all_traces:
        target = None

    for evt in events:
        if target and str(evt.get("trace_id") or "") != target:
            continue
        print(_format(evt))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
