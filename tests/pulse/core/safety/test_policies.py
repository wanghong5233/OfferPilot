"""Contract tests for SafetyPlane Python policies.

测试覆盖面 = 三个 policy 的核心判决分支 + 两个辅助函数的边界.

**为什么不测 "Decision 正确序列化" 这类 sanity?**

Decision / AskRequest / ResumeHandle 的不变式由 ``test_contracts.py``
保证, 本文件只关注 "给定 Intent + PermissionContext, policy 返回的分支
是否和 docstring 描述一致". 凡是把 policy 翻成正确 Decision 子类型的
责任, 落在 Decision.__post_init__ + 这里的 kind 断言上.
"""

from __future__ import annotations

import pytest

from pulse.core.safety import (
    DEFAULT_RESUME_INTENT,
    DEFAULT_RESUME_PAYLOAD_SCHEMA,
    Intent,
    PermissionContext,
    card_policy,
    profile_covers,
    reply_policy,
    send_resume_policy,
    session_approved,
)


def _ctx(**overrides) -> PermissionContext:
    defaults: dict = {
        "module": "job_chat",
        "task_id": "task-abc",
        "trace_id": "trace-xyz",
        "user_id": "u_alice",
        "profile_view": {},
        "session_approvals": frozenset(),
    }
    defaults.update(overrides)
    return PermissionContext(**defaults)


# ── profile_covers ─────────────────────────────────────────────────────


class TestProfileCovers:
    def test_returns_true_when_all_keys_have_nonempty_values(self) -> None:
        assert profile_covers({"a": "x", "b": 1}, ("a", "b")) is True

    def test_empty_keys_iter_treated_as_covered(self) -> None:
        # 空迭代 = "不要求证据", 应 True (让 policy 在 evidence_keys=() 时
        # 走到 "无豁免 → ask" 分支, 而不是异常 False).
        assert profile_covers({}, ()) is True

    @pytest.mark.parametrize(
        "profile,keys",
        [
            ({}, ("a",)),
            ({"a": None}, ("a",)),
            ({"a": ""}, ("a",)),
            ({"a": "   "}, ("a",)),
            ({"a": "x"}, ("a", "b")),
        ],
    )
    def test_missing_or_empty_values_return_false(self, profile, keys) -> None:
        assert profile_covers(profile, keys) is False

    def test_rejects_non_string_key(self) -> None:
        assert profile_covers({"a": "x"}, (123,)) is False  # type: ignore[arg-type]


# ── session_approved ───────────────────────────────────────────────────


class TestSessionApproved:
    def test_true_when_token_in_set(self) -> None:
        ctx = _ctx(session_approvals=frozenset({"reply:conv-1:h123"}))
        assert session_approved(ctx, "reply:conv-1:h123") is True

    def test_false_when_token_missing(self) -> None:
        ctx = _ctx(session_approvals=frozenset({"reply:conv-1:h123"}))
        assert session_approved(ctx, "reply:conv-1:other") is False

    def test_empty_token_rejected(self) -> None:
        assert session_approved(_ctx(), "") is False
        assert session_approved(_ctx(), "   ") is False


# ── reply_policy ───────────────────────────────────────────────────────


class TestReplyPolicy:
    def _intent(self, **over) -> Intent:
        args = {
            "conversation_id": "conv-1",
            "hr_label": "腾讯-张三",
            "hr_message": "今天下午方便面试吗?",
            "draft_text": "您好,方便,几点?",
            "draft_hash": "h123",
        }
        args.update(over)
        return Intent(kind="mutation", name="job.chat.reply", args=args)

    def test_no_approval_no_evidence_allows_planner_reply(self) -> None:
        decision = reply_policy(self._intent(), _ctx())
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.reply.planner_allow"

    def test_session_approval_for_same_draft_yields_allow(self) -> None:
        ctx = _ctx(session_approvals=frozenset({"reply:conv-1:h123"}))
        decision = reply_policy(self._intent(), ctx)
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.reply.session_approval"

    def test_session_approval_scoped_to_conversation(self) -> None:
        # 错误 session token 不应被当作 session_approved, 但 planner 已选择
        # reply 且未声明 evidence_keys 时仍可走默认 allow。
        ctx = _ctx(session_approvals=frozenset({"reply:conv-OTHER:h123"}))
        decision = reply_policy(self._intent(), ctx)
        assert decision.kind == "allow"

    def test_session_approval_scoped_to_draft_hash(self) -> None:
        # 错误 draft token 不应命中 session_approved; 默认 allow 来自 planner,
        # 不是来自旧授权复用。
        ctx = _ctx(session_approvals=frozenset({"reply:conv-1:OLDHASH"}))
        decision = reply_policy(self._intent(draft_hash="NEWHASH"), ctx)
        assert decision.kind == "allow"

    def test_profile_evidence_allows_when_evidence_keys_declared(self) -> None:
        intent = Intent(
            kind="mutation",
            name="job.chat.reply",
            args={
                "conversation_id": "conv-1",
                "draft_text": "方便,周二下午 3 点可以。",
                "draft_hash": "hZ",
            },
            evidence_keys=("user_weekday_afternoon_availability",),
        )
        ctx = _ctx(
            profile_view={"user_weekday_afternoon_availability": "工作日下午都可"}
        )
        decision = reply_policy(intent, ctx)
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.reply.profile_evidence"

    def test_profile_evidence_missing_falls_back_to_ask(self) -> None:
        intent = Intent(
            kind="mutation",
            name="job.chat.reply",
            args={
                "conversation_id": "conv-1",
                "draft_text": "可以。",
                "draft_hash": "h",
            },
            evidence_keys=("user_weekday_afternoon_availability",),
        )
        # profile 中没 key → 不算证据, ask.
        decision = reply_policy(intent, _ctx(profile_view={}))
        assert decision.kind == "ask"


# ── send_resume_policy ─────────────────────────────────────────────────


class TestSendResumePolicy:
    def _intent(self, **over) -> Intent:
        args = {
            "conversation_id": "conv-1",
            "hr_id": "hr-xyz",
            "hr_label": "腾讯-张三",
            "resume_profile_id": "profile-default",
        }
        args.update(over)
        return Intent(kind="mutation", name="job.chat.send_resume", args=args)

    def test_defaults_to_allow_predefined_resume_action(self) -> None:
        decision = send_resume_policy(self._intent(), _ctx())
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.send_resume.predefined_allow"

    def test_session_approval_scoped_to_hr_id(self) -> None:
        ctx = _ctx(session_approvals=frozenset({"resume:hr-xyz"}))
        decision = send_resume_policy(self._intent(), ctx)
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.send_resume.session_approval"

    def test_session_approval_on_different_hr_does_not_leak(self) -> None:
        ctx = _ctx(session_approvals=frozenset({"resume:hr-OTHER"}))
        decision = send_resume_policy(self._intent(), ctx)
        assert decision.kind == "allow"

    def test_missing_hr_id_does_not_crash_and_allows(self) -> None:
        decision = send_resume_policy(self._intent(hr_id=""), _ctx())
        assert decision.kind == "allow"


# ── card_policy ────────────────────────────────────────────────────────


class TestCardPolicy:
    def _intent(self, **over) -> Intent:
        args = {
            "conversation_id": "conv-1",
            "card_type": "interview_invite",
            "card_type_human": "面试邀请",
            "card_title": "Python 后端开发面试",
            "suggested_action": "接受",
        }
        args.update(over)
        return Intent(
            kind="mutation", name="job.chat.card.accept", args=args
        )

    def test_always_asks_even_with_session_approval(self) -> None:
        # 卡片动作永远 ask, session_approvals 不会放行 —— 面试卡片涉及
        # 时间承诺, 不允许 "同意接受一次后自动接受所有卡片".
        ctx = _ctx(session_approvals=frozenset({"card:interview_invite"}))
        decision = card_policy(self._intent(), ctx)
        assert decision.kind == "ask"

    def test_exchange_resume_card_allows_predefined_action(self) -> None:
        decision = card_policy(
            self._intent(card_type="exchange_resume", card_type_human="换简历请求"),
            _ctx(),
        )
        assert decision.kind == "allow"
        assert decision.rule_id == "job_chat.card.predefined_allow"

    def test_ask_content_surfaces_card_type_human_and_title(self) -> None:
        decision = card_policy(self._intent(), _ctx())
        assert decision.kind == "ask"
        assert decision.ask_request is not None
        q = decision.ask_request.question
        assert "面试邀请" in q
        assert "Python 后端开发面试" in q

    def test_missing_optional_fields_do_not_crash(self) -> None:
        # card_title / suggested_action 为空 不应抛, 用默认文案兜底.
        decision = card_policy(
            self._intent(card_title="", suggested_action=""), _ctx()
        )
        assert decision.kind == "ask"
