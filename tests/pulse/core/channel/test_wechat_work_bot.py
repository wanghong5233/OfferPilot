from __future__ import annotations

from pulse.core.channel.wechat_work_bot import _extract_reply


def test_extract_reply_uses_nested_brain_answer() -> None:
    result = {
        "channel": "wechat-work-bot",
        "user_id": "u1",
        "text": "开启自动投递服务",
        "result": {"answer": "已开启自动投递服务。"},
    }

    assert _extract_reply(result) == "已开启自动投递服务。"


def test_extract_reply_prefers_standard_dispatch_reply_field() -> None:
    result = {
        "channel": "wechat-work-bot",
        "user_id": "u1",
        "text": "开启自动投递服务",
        "reply": "已开启后台任务 job_greet.patrol。",
        "result": {
            "ok": True,
            "name": "job_greet.patrol",
            "enabled": True,
        },
    }

    assert _extract_reply(result) == "已开启后台任务 job_greet.patrol。"


def test_extract_reply_never_echoes_dispatch_envelope_text() -> None:
    result = {
        "channel": "wechat-work-bot",
        "user_id": "u1",
        "text": "开启自动投递服务",
        "brain": {"answer": ""},
        "result": {"text": "业务结果里的 text 也不能发"},
        "error": "upstream failed",
    }

    assert _extract_reply(result) == ""


def test_extract_reply_accepts_top_level_answer_but_not_top_level_text() -> None:
    assert _extract_reply({"answer": "ok"}) == "ok"
    assert _extract_reply({"text": "user input"}) == ""


def test_extract_reply_surfaces_envelope_error_when_brain_absent() -> None:
    result = {
        "channel": "wechat-work-bot",
        "user_id": "u1",
        "text": "开启自动投递服务",
        "trace_id": "trace_xyz",
        "error": "RuntimeError: patrol not found",
        "mode": "brain",
    }

    reply = _extract_reply(result)
    assert "RuntimeError" in reply
    assert "trace_xyz" in reply
