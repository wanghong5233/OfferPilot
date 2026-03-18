"""公共时间工具 — 统一使用北京时间。

WSL 默认时区为 UTC，直接 datetime.now() 会导致所有时间相关逻辑
（调度、日志、通知、截图文件名）使用错误时区。
"""

from __future__ import annotations

import os
from datetime import datetime


def now_beijing() -> datetime:
    """返回当前北京时间（可通过 GUARD_TIMEZONE 覆盖）。"""
    tz_name = os.getenv("GUARD_TIMEZONE", "Asia/Shanghai")
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        try:
            import pytz
            tz = pytz.timezone(tz_name)
        except Exception:
            return datetime.now()
    return datetime.now(tz)
