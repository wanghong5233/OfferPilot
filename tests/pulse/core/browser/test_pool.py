from __future__ import annotations

from pulse.core.browser.pool import BrowserPool


class _Session:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_browser_pool_reuses_session_by_key() -> None:
    pool = BrowserPool(ttl_seconds=600)
    created: list[str] = []

    def factory(key: str) -> _Session:
        created.append(key)
        return _Session(token=f"sess-{len(created)}")

    s1 = pool.get("boss", factory=factory)
    s2 = pool.get("boss", factory=factory)

    assert s1 is s2
    assert created == ["boss"]


def test_browser_pool_closes_released_session() -> None:
    pool = BrowserPool(ttl_seconds=600)
    session = pool.get("boss", factory=lambda _: _Session("sess-1"))
    pool.release("boss")
    assert session.closed == 1


def test_browser_pool_recreates_when_unhealthy() -> None:
    health = {"ok": True}
    pool = BrowserPool(ttl_seconds=600, health_check=lambda _: health["ok"])
    count = {"n": 0}

    def factory(_: str) -> _Session:
        count["n"] += 1
        return _Session(token=f"sess-{count['n']}")

    s1 = pool.get("boss", factory=factory)
    health["ok"] = False
    s2 = pool.get("boss", factory=factory)
    assert s1 is not s2
