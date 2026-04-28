"""Hard contract: planner decisions come from LLM output, not heuristics."""

from __future__ import annotations

from pulse.modules.job.chat.planner import HrMessagePlanner, PlannedChatAction
from pulse.modules.job.shared.enums import ChatAction, ConversationInitiator


class _LLM:
    def __init__(self, response) -> None:  # type: ignore[no-untyped-def]
        self.response = response
        self.routes: list[str] = []

    def invoke_json(self, messages, *, route):  # type: ignore[no-untyped-def]
        self.routes.append(route)
        return self.response


def test_llm_classifies_hr_greeting_as_send_resume() -> None:
    llm = _LLM({"action": "send_resume", "reason": "HR 主动打招呼", "reply_text": ""})
    plan = HrMessagePlanner(llm).plan(message="你好，对你之前的经历非常感兴趣")

    assert plan.action == ChatAction.SEND_RESUME
    assert llm.routes == ["job_chat"]


def test_llm_failure_escalates_instead_of_heuristic_send_resume() -> None:
    plan = HrMessagePlanner(_LLM("not-json")).plan(message="你好")

    assert plan.action == ChatAction.ESCALATE
    assert plan.reason == "llm_required_no_heuristic_chat_action"


def test_exchange_resume_card_is_platform_signal_not_semantic_heuristic() -> None:
    plan = HrMessagePlanner(_LLM("should-not-be-called")).plan(
        message="",
        has_exchange_resume_card=True,
    )

    assert plan.action == ChatAction.ACCEPT_CARD


# ────────────────────────────────────────────────────────────────────
# 2. initiator policy: the "HR-initiated → force escalate" one-liner
#    has been explicitly retired. Contract locks that it is NOT back.
# ────────────────────────────────────────────────────────────────────


def test_initiator_policy_no_longer_force_escalates_hr_reply() -> None:
    """HR 主动发起的对话不再被一刀切升级成 ESCALATE.

    这是历史上 "3 未读全部静默 ESCALATE, 0 自动回复" 的根本原因之一,
    与用户诉求 ('HR 打招呼先发简历') 直接冲突, 故永久下线.
    """
    plan = PlannedChatAction(
        action=ChatAction.REPLY,
        reason="LLM says reply",
        reply_text="您好",
    )
    policed = HrMessagePlanner._apply_initiator_policy(
        plan, initiated_by=ConversationInitiator.HR
    )
    assert policed.action == ChatAction.REPLY
    assert policed.reply_text == "您好"


def test_initiator_policy_no_longer_force_escalates_hr_send_resume() -> None:
    plan = PlannedChatAction(
        action=ChatAction.SEND_RESUME, reason="planner says send_resume"
    )
    policed = HrMessagePlanner._apply_initiator_policy(
        plan, initiated_by=ConversationInitiator.HR
    )
    assert policed.action == ChatAction.SEND_RESUME


def test_initiator_policy_does_not_rewrite_llm_ignore() -> None:
    plan = PlannedChatAction(
        action=ChatAction.IGNORE, reason="low-priority greeting"
    )
    policed = HrMessagePlanner._apply_initiator_policy(
        plan, initiated_by=ConversationInitiator.HR
    )
    assert policed.action == ChatAction.IGNORE
    assert policed.reason == "low-priority greeting"


def test_initiator_policy_keeps_ignore_on_ui_noise() -> None:
    """纯 UI 噪音 / empty 的 IGNORE 必须保持 IGNORE, 不要给噪音发简历."""
    for reason in (
        "BOSS 系统 UI 噪音, 非真实 HR 消息",
        "empty message",
        "LLM says system pure UI text",
    ):
        plan = PlannedChatAction(action=ChatAction.IGNORE, reason=reason)
        policed = HrMessagePlanner._apply_initiator_policy(
            plan, initiated_by=ConversationInitiator.HR
        )
        assert policed.action == ChatAction.IGNORE, (
            f"IGNORE with reason={reason!r} must stay IGNORE; got {policed.action.value}"
        )


def test_initiator_policy_keeps_escalate_for_sensitive_topics() -> None:
    plan = PlannedChatAction(action=ChatAction.ESCALATE, reason="薪资谈判")
    policed = HrMessagePlanner._apply_initiator_policy(
        plan, initiated_by=ConversationInitiator.HR
    )
    assert policed.action == ChatAction.ESCALATE
