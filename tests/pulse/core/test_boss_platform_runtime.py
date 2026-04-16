from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_runtime_module():
    root = Path(__file__).resolve().parents[3]
    runtime_path = root / "backend" / "mcp_servers" / "_boss_platform_runtime.py"
    spec = importlib.util.spec_from_file_location("pulse_boss_runtime_test", runtime_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load boss platform runtime module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = _load_runtime_module()


def test_runtime_reply_browser_mode_uses_executor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_ACTION_AUDIT_PATH", str(tmp_path / "boss_actions.jsonl"))
    monkeypatch.setenv("PULSE_BOSS_MCP_REPLY_MODE", "browser")

    def _fake_execute_browser_reply(
        *,
        conversation_id: str,
        reply_text: str,
        profile_id: str,
        conversation_hint: dict | None = None,
    ) -> dict:
        assert conversation_id == "conv-1"
        assert "测试回复" in reply_text
        assert profile_id == "default"
        assert isinstance(conversation_hint, dict)
        return {
            "ok": True,
            "status": "sent",
            "source": "boss_mcp_browser",
            "error": None,
        }

    monkeypatch.setattr(runtime, "_execute_browser_reply", _fake_execute_browser_reply)
    result = runtime.reply_conversation(
        conversation_id="conv-1",
        reply_text="测试回复",
        profile_id="default",
    )
    assert result["ok"] is True
    assert result["status"] == "sent"
    assert result["source"] == "boss_mcp_browser"


def test_runtime_greet_browser_mode_uses_executor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_ACTION_AUDIT_PATH", str(tmp_path / "boss_actions.jsonl"))
    monkeypatch.setenv("PULSE_BOSS_MCP_GREET_MODE", "browser")

    def _fake_execute_browser_greet(*, run_id: str, job_id: str, source_url: str, greeting_text: str) -> dict:
        assert run_id == "run-1"
        assert job_id == "job-1"
        assert source_url.startswith("https://")
        assert greeting_text
        return {
            "ok": True,
            "status": "sent",
            "source": "boss_mcp_browser",
            "error": None,
        }

    monkeypatch.setattr(runtime, "_execute_browser_greet", _fake_execute_browser_greet)
    result = runtime.greet_job(
        run_id="run-1",
        job_id="job-1",
        source_url="https://www.zhipin.com/job_detail/abc",
        job_title="AI Agent Intern",
        company="Pulse Labs",
        greeting_text="你好，我想了解岗位详情",
    )
    assert result["ok"] is True
    assert result["status"] == "sent"
    assert result["source"] == "boss_mcp_browser"


def test_runtime_health_includes_browser_config(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_BROWSER_PROFILE_DIR", "./backend/.playwright/boss")
    monkeypatch.setenv("PULSE_BOSS_BROWSER_HEADLESS", "false")
    monkeypatch.setenv("PULSE_BOSS_BROWSER_TIMEOUT_MS", "15000")
    monkeypatch.setenv("PULSE_BOSS_BROWSER_STEALTH_ENABLED", "true")
    monkeypatch.setenv("PULSE_BOSS_BROWSER_BLOCK_IFRAME_CORE", "true")
    health = runtime.health()
    assert health["ok"] is True
    assert "browser" in health
    assert isinstance(health["browser"]["profile_dir"], str)
    assert int(health["browser"]["timeout_ms"]) >= 3000
    assert "stealth_enabled" in health["browser"]
    assert "block_iframe_core" in health["browser"]
    assert "scan_mode" in health
    assert "pull_mode" in health


def test_runtime_check_login_ready(monkeypatch) -> None:
    class _FakePage:
        url = "https://www.zhipin.com/web/geek/chat"

        def goto(self, url: str, wait_until: str, timeout: int) -> None:  # noqa: ANN001
            _ = url, wait_until, timeout
            self.url = "https://www.zhipin.com/web/geek/chat"

        def inner_text(self, selector: str) -> str:
            _ = selector
            return "正常聊天页面"

    monkeypatch.setattr(runtime, "_ensure_browser_page", lambda: _FakePage())
    result = runtime.check_login(check_url="https://www.zhipin.com/web/geek/chat")
    assert result["ok"] is True
    assert result["status"] == "ready"


def test_runtime_check_login_auth_required(monkeypatch) -> None:
    class _FakePage:
        url = "https://www.zhipin.com/web/user/login"

        def goto(self, url: str, wait_until: str, timeout: int) -> None:  # noqa: ANN001
            _ = url, wait_until, timeout
            self.url = "https://www.zhipin.com/web/user/login"

        def inner_text(self, selector: str) -> str:
            _ = selector
            return "请登录"

    monkeypatch.setattr(runtime, "_ensure_browser_page", lambda: _FakePage())
    result = runtime.check_login(check_url="https://www.zhipin.com/web/geek/chat")
    assert result["ok"] is False
    assert result["status"] == "auth_required"


def test_runtime_scan_jobs_browser_first_prefers_browser(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_SCAN_MODE", "browser_first")

    def _fake_scan_jobs_via_browser(*, keyword: str, max_items: int, max_pages: int) -> dict:
        assert "AI Agent" in keyword
        assert max_items == 3
        assert max_pages == 2
        return {
            "ok": True,
            "status": "ready",
            "items": [
                {
                    "job_id": "job-1",
                    "title": "AI Agent 实习生",
                    "company": "Pulse Labs",
                    "salary": "15K-25K",
                    "source_url": "https://www.zhipin.com/job_detail/1",
                    "snippet": "职位描述",
                    "source": "boss_mcp_browser_scan",
                }
            ],
            "pages_scanned": 1,
            "source": "boss_mcp_browser_scan",
            "errors": [],
        }

    monkeypatch.setattr(runtime, "_scan_jobs_via_browser", _fake_scan_jobs_via_browser)
    result = runtime.scan_jobs(keyword="AI Agent", max_items=3, max_pages=2)
    assert result["ok"] is True
    assert result["source"] == "boss_mcp_browser_scan"
    assert result["mode"] == "browser_first"
    assert len(result["items"]) == 1


def test_runtime_scan_jobs_browser_only_skips_web_search_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_SCAN_MODE", "browser_only")

    monkeypatch.setattr(
        runtime,
        "_scan_jobs_via_browser",
        lambda **_: {
            "ok": False,
            "status": "executor_unavailable",
            "items": [],
            "pages_scanned": 1,
            "source": "boss_mcp_browser_scan",
            "errors": ["browser down"],
        },
    )

    def _unexpected_search_web(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("search_web should not be called in browser_only mode")

    monkeypatch.setattr(runtime, "search_web", _unexpected_search_web)
    result = runtime.scan_jobs(keyword="AI Agent", max_items=3, max_pages=2)
    assert result["ok"] is False
    assert result["items"] == []
    assert result["source"] == "boss_mcp_browser_scan"
    assert result["mode"] == "browser_only"
    assert "browser down" in result["errors"]


def test_runtime_pull_conversations_browser_first_fallback_local(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_PULL_MODE", "browser_first")
    inbox_path = tmp_path / "boss_chat_inbox.jsonl"
    inbox_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "conversation_id": "conv-1",
                        "hr_name": "王HR",
                        "company": "Pulse Labs",
                        "job_title": "AI Agent 实习生",
                        "latest_message": "你好，方便聊一下吗",
                        "latest_time": "刚刚",
                        "unread_count": 2,
                    },
                    ensure_ascii=False,
                )
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PULSE_BOSS_CHAT_INBOX_PATH", str(inbox_path))

    monkeypatch.setattr(
        runtime,
        "_pull_conversations_via_browser",
        lambda **_: {
            "ok": False,
            "status": "executor_unavailable",
            "items": [],
            "unread_total": 0,
            "source": "boss_mcp_browser_chat",
            "errors": ["browser down"],
        },
    )

    result = runtime.pull_conversations(
        max_conversations=10,
        unread_only=False,
        fetch_latest_hr=False,
        chat_tab="all",
    )
    assert result["ok"] is True
    assert result["source"] == "boss_mcp_local_inbox"
    assert result["mode"] == "browser_first"
    assert "browser down" in result["errors"]
    assert len(result["items"]) == 1


def test_runtime_pull_conversations_browser_only_skips_local_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PULSE_BOSS_MCP_PULL_MODE", "browser_only")
    inbox_path = tmp_path / "boss_chat_inbox.jsonl"
    inbox_path.write_text(
        json.dumps(
            {
                "conversation_id": "conv-1",
                "hr_name": "王HR",
                "company": "Pulse Labs",
                "job_title": "AI Agent 实习生",
                "latest_message": "你好，方便聊一下吗",
                "latest_time": "刚刚",
                "unread_count": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PULSE_BOSS_CHAT_INBOX_PATH", str(inbox_path))
    monkeypatch.setattr(
        runtime,
        "_pull_conversations_via_browser",
        lambda **_: {
            "ok": False,
            "status": "executor_unavailable",
            "items": [],
            "unread_total": 0,
            "source": "boss_mcp_browser_chat",
            "errors": ["browser down"],
        },
    )

    result = runtime.pull_conversations(
        max_conversations=10,
        unread_only=False,
        fetch_latest_hr=False,
        chat_tab="all",
    )
    assert result["ok"] is False
    assert result["items"] == []
    assert result["source"] == "boss_mcp_browser_chat"
    assert result["mode"] == "browser_only"
    assert "browser down" in result["errors"]


def test_runtime_pull_conversations_browser_text_fallback(monkeypatch) -> None:
    class _FakePage:
        url = "https://www.zhipin.com/web/geek/chat"

        def goto(self, url: str, wait_until: str, timeout: int) -> None:  # noqa: ANN001
            _ = url, wait_until, timeout
            self.url = "https://www.zhipin.com/web/geek/chat"

        def inner_text(self, selector: str) -> str:
            _ = selector
            return (
                "首页\n消息\n全部\n未读\n新招呼\n"
                "00:18\n王雨城蜂屿科技创始人\nHi！王鸿，恭喜通过初筛。\n"
                "03月25日\n姚先生曹操出行高级招聘\n[送达]\n您好，方便沟通吗\n"
            )

        def eval_on_selector_all(self, selector: str, script: str):  # noqa: ANN001
            _ = selector, script
            return []

        def wait_for_selector(self, selector: str, timeout: int):  # noqa: ANN001
            _ = selector, timeout
            raise RuntimeError("selector not found")

        def locator(self, selector: str):  # noqa: ANN001
            _ = selector
            raise RuntimeError("selector not found")

        def wait_for_timeout(self, ms: int) -> None:
            _ = ms

    monkeypatch.setattr(runtime, "_ensure_browser_page", lambda: _FakePage())
    result = runtime._pull_conversations_via_browser(
        max_conversations=5,
        unread_only=False,
        fetch_latest_hr=False,
        chat_tab="全部",
    )
    assert result["ok"] is True
    assert result["source"] == "boss_mcp_browser_chat"
    items = result.get("items") or []
    assert len(items) >= 1
    assert str(items[0].get("latest_message") or "")


def test_runtime_detect_runtime_risk_security_url() -> None:
    class _FakePage:
        def inner_text(self, selector: str) -> str:
            _ = selector
            return "正在加载中"

    status = runtime._detect_runtime_risk(
        _FakePage(),
        current_url="https://www.zhipin.com/web/passport/zp/security.html?code=37",
    )
    assert status == "risk_blocked"


def test_runtime_build_search_url_candidates() -> None:
    urls = runtime._build_search_url_candidates(keyword="AI Agent 实习", page=1)
    assert isinstance(urls, list)
    assert len(urls) >= 1
    assert any("/web/geek/jobs?" in url or "/web/geek/job?" in url for url in urls)


def test_runtime_extract_job_leads_from_chat_page(monkeypatch) -> None:
    class _FakePage:
        def inner_text(self, selector: str) -> str:
            _ = selector
            return (
                "首页\n消息\n全部\n未读\n新招呼\n"
                "00:18\n王雨城蜂屿科技创始人\nHi！王鸿，恭喜通过初筛。\n"
                "03月25日\n姚先生曹操出行高级招聘\n[送达]\n您好，方便沟通吗\n"
            )

    monkeypatch.setattr(runtime, "_build_chat_url", lambda cid: f"https://www.zhipin.com/web/geek/chat?conversationId={cid}")
    rows = runtime._extract_job_leads_from_chat_page(
        _FakePage(),
        keyword="AI Agent 实习",
        max_items=3,
        seen_keys=set(),
    )
    assert isinstance(rows, list)
    assert len(rows) >= 1
    first = rows[0]
    assert str(first.get("source") or "") == "boss_mcp_browser_chat_lead"
    assert str(first.get("source_url") or "").startswith("https://www.zhipin.com/web/geek/chat")
