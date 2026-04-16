from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def safe_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min_value, min(parsed, max_value))


def safe_float(value: Any, default: float, *, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(min_value, min(parsed, max_value))


def http_get_json(
    url: str,
    *,
    timeout_sec: float = 8.0,
    headers: dict[str, str] | None = None,
) -> Any:
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(2.0, timeout_sec)) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"http {exc.code}: {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"url error: {exc.reason}") from exc
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"invalid json response: {text[:200]}") from exc
