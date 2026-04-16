from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pulse.core.browser.auth import check_login_required, handle_cookie_expired


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, *, url: str, login_count: int = 0) -> None:
        self.url = url
        self._login_count = login_count
        self.screenshots: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:  # noqa: ARG002
        return _FakeLocator(self._login_count)

    def screenshot(self, *, path: str) -> None:
        self.screenshots.append(path)


def test_check_login_required_detects_url_markers() -> None:
    page = _FakePage(url="https://www.zhipin.com/web/user/login")
    assert check_login_required(page) is True


def test_check_login_required_detects_login_form() -> None:
    page = _FakePage(url="https://www.zhipin.com/web/geek/chat", login_count=1)
    assert check_login_required(page) is True


def test_handle_cookie_expired_runs_callbacks(tmp_path: Path) -> None:
    page = _FakePage(url="https://www.zhipin.com/web/geek/chat")
    events: list[tuple[str, str]] = []
    markers = {"notified": 0, "reset": 0}

    result = handle_cookie_expired(
        page,
        operation="聊天列表拉取",
        screenshot_dir=tmp_path,
        emit=lambda t, m, p: events.append((t, m)),  # noqa: ARG005
        notify=lambda: markers.__setitem__("notified", markers["notified"] + 1),
        reset_session=lambda: markers.__setitem__("reset", markers["reset"] + 1),
        now=lambda: datetime(2026, 1, 2, 3, 4, 5),
    )

    assert result is not None
    assert result.name.startswith("cookie_expired_20260102_030405")
    assert markers["notified"] == 1
    assert markers["reset"] == 1
    assert events[0][0] == "error"
    assert events[1][0] == "browser_screenshot"
