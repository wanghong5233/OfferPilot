"""Classify an inbound HR message into a chat action.

The planner is a *policy* component, not a service. It owns no IO other
than invoking the LLM router; it is pure logic otherwise and can be used
in unit tests or offline simulations with a stub router.

Output is always a :class:`PlannedChatAction` whose ``action`` field is
drawn from :class:`pulse.modules.job.shared.enums.ChatAction`.

求职场景第一性原理 — "HR 抛球 = 递简历":
    从求职者视角看, HR 主动给你发任何消息 (包括 "你好对你的经历感兴趣"
    这种纯寒暄) 都等价于 "请给我简历". 保守地 IGNORE 等于错失机会,
    保守地 ESCALATE 到人工又让长程自动回复失去意义. 因此 planner 的
    默认积极动作是 ``send_resume``; 只有两种情况例外:
      * UI 系统噪音 (如 "您正在与 Boss 某某沟通" / "对方已暂停沟通") → IGNORE
      * 需要用户实时事实或谈判承诺的话题 → ESCALATE
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pulse.core.llm.router import LLMRouter
from pulse.core.tokenizer import token_preview

from ..memory import JobMemorySnapshot
from ..shared.enums import CardAction, CardType, ChatAction, ConversationInitiator

logger = logging.getLogger(__name__)


_LLM_ACTION_WHITELIST: frozenset[str] = frozenset(
    {
        ChatAction.REPLY.value,
        ChatAction.SEND_RESUME.value,
        ChatAction.ACCEPT_CARD.value,
        ChatAction.REJECT_CARD.value,
        ChatAction.ESCALATE.value,
        ChatAction.IGNORE.value,
    }
)


@dataclass(frozen=True, slots=True)
class PlannedChatAction:
    action: ChatAction
    reason: str
    reply_text: str | None = None
    card_type: CardType | None = None
    card_action: CardAction | None = None


class HrMessagePlanner:
    """LLM-backed classifier; invalid LLM output escalates to user input."""

    def __init__(self, llm_router: LLMRouter) -> None:
        self._llm = llm_router

    # ---------------------------------------------------------------- public

    def plan(
        self,
        *,
        message: str,
        has_exchange_resume_card: bool = False,
        initiated_by: ConversationInitiator = ConversationInitiator.SELF,
        snapshot: JobMemorySnapshot | None = None,
        company: str = "",
        job_title: str = "",
    ) -> PlannedChatAction:
        """Classify an HR message into a chat action.

        ``snapshot`` / ``company`` / ``job_title`` 是可选上下文: 当提供时, 分类
        器会把用户偏好 + 当前岗位元数据写进 system prompt, 让 LLM 能识别
        "HR 来自用户刚屏蔽的公司" 之类的场景并升级为 escalate; 不提供时
        退化到纯消息分类, 用于不持有 snapshot 的调用方 (如单元测试)。
        """
        safe_message = str(message or "").strip()
        if has_exchange_resume_card:
            # HR sent an explicit UI-level request — skip LLM guessing and
            # surface it for HITL confirmation by suggesting accept.
            return PlannedChatAction(
                action=ChatAction.ACCEPT_CARD,
                reason="HR sent an 'exchange_resume' card",
                card_type=CardType.EXCHANGE_RESUME,
                card_action=CardAction.ACCEPT,
            )
        if not safe_message:
            return PlannedChatAction(
                action=ChatAction.IGNORE,
                reason="empty message",
            )
        llm_plan = self._plan_with_llm(
            safe_message,
            snapshot=snapshot,
            company=company,
            job_title=job_title,
        )
        plan = llm_plan if llm_plan is not None else PlannedChatAction(
            action=ChatAction.ESCALATE,
            reason="llm_required_no_heuristic_chat_action",
        )
        return self._apply_initiator_policy(plan, initiated_by=initiated_by)

    # ---------------------------------------------------------------- LLM

    def _plan_with_llm(
        self,
        message: str,
        *,
        snapshot: JobMemorySnapshot | None,
        company: str,
        job_title: str,
    ) -> PlannedChatAction | None:
        snapshot_md = snapshot.to_prompt_section() if snapshot is not None else "(no preferences set)"
        context_lines: list[str] = []
        if company.strip():
            context_lines.append(f"- company: {company.strip()}")
        if job_title.strip():
            context_lines.append(f"- job_title: {job_title.strip()}")
        context_md = "\n".join(context_lines) or "- (no prior context)"

        system_prompt = (
            "You classify inbound HR messages for an AI job-seeking assistant. "
            "The user is actively looking for jobs — their proactive default "
            "when an HR reaches out is to send the resume, NOT to go silent. "
            "Pick the single safest action in this priority order:\n"
            "  1. If HR represents a company/keyword the user has blocked → escalate "
            "(do NOT auto reply and do NOT send the resume).\n"
            "  2. Questions that require the user's real-time facts or commitments "
            "(今天/明天是否有空, 具体面试时间, 电话沟通时间, 具体薪资谈判, offer 比较, "
            "到岗细节) → escalate for user input.\n"
            "  3. Interactive card cue (HR sent 交换简历 card etc.) → accept_card "
            "(already handled upstream — only pick this if the text literally "
            "quotes a card prompt).\n"
            "  4. If HR explicitly asks for resume/作品集/附件 → send_resume.\n"
            "  5. If HR opens the conversation with greetings / interest / any "
            "首次问候 / 简单自我介绍 (including 寒暄类 'hi 我们正在招 xxx') → "
            "send_resume. Being a job seeker, not sending the resume when the HR "
            "starts a chat is a lost opportunity; this is the active default.\n"
            "  6. If the HR message is a concrete factual question the user's "
            "profile clearly answers (技术栈 / 学校 / 是否能到岗 etc.) → reply "
            "with a concise 中文 response.\n"
            "  7. Only pick ignore for pure system UI text like '您正在与 Boss "
            "X 沟通' / '对方已暂停沟通' / 纯表情. Never pick ignore for a real "
            "HR utterance, even a short one.\n\n"
            "Return ONLY valid JSON. Schema:\n"
            '{"action":"reply|send_resume|accept_card|reject_card|escalate|ignore",'
            '"reason":"<one line, 中文 or English>","reply_text":"<中文 reply, '
            'or empty string when action != reply>"}\n\n'
            f"## User preferences\n{snapshot_md}"
        )
        user_prompt = (
            f"## Conversation context\n{context_md}\n\n"
            f"## Latest HR message\n{token_preview(message, max_tokens=800)}\n\n"
            "Return JSON only."
        )

        parsed = self._llm.invoke_json(
            [
                _system(system_prompt),
                _user(user_prompt),
            ],
            route="job_chat",
        )
        if not isinstance(parsed, dict):
            return None
        raw_action = str(parsed.get("action") or "").strip().lower()
        if raw_action not in _LLM_ACTION_WHITELIST:
            return None
        action = ChatAction(raw_action)
        reason = str(parsed.get("reason") or "").strip() or "llm_classification"
        reply_text = str(parsed.get("reply_text") or "").strip() or None
        return PlannedChatAction(action=action, reason=reason, reply_text=reply_text)

    # ---------------------------------------------------------------- policy

    @staticmethod
    def _apply_initiator_policy(
        plan: PlannedChatAction,
        *,
        initiated_by: ConversationInitiator,
    ) -> PlannedChatAction:
        """Post-filter based on who opened the conversation."""
        _ = initiated_by
        return plan


def _system(content: str) -> object:
    from langchain_core.messages import SystemMessage
    return SystemMessage(content=content)


def _user(content: str) -> object:
    from langchain_core.messages import HumanMessage
    return HumanMessage(content=content)


__all__ = ["HrMessagePlanner", "PlannedChatAction"]
