from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .compaction import CompactionEngine
from .cost import CostController
from .hooks import HookPoint, HookRegistry
from .llm.router import LLMRouter
from .memory.envelope import MemoryLayer
from .prompt_contract import PromptContractBuilder
from .task_context import StopReason, TaskContext
from .tool import ToolRegistry


def _sanitize_tool_name(name: str) -> str:
    """OpenAI function names: ^[a-zA-Z0-9_-]+$. Replace dots/spaces."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(name or ""))


def _normalize_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    safe = dict(schema or {})
    if safe.get("type") != "object":
        safe = {"type": "object", "properties": dict(safe.get("properties") or {})}
    if "properties" not in safe:
        safe["properties"] = {}
    return safe


@dataclass(slots=True)
class BrainStep:
    index: int
    thought: str
    action: str
    tool_name: str | None
    tool_args: dict[str, Any]
    observation: Any


@dataclass(slots=True)
class BrainRunResult:
    answer: str
    used_tools: list[str]
    steps: list[BrainStep]
    stopped_reason: StopReason

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "used_tools": list(self.used_tools),
            "steps": [asdict(step) for step in self.steps],
            "stopped_reason": self.stopped_reason.value if isinstance(self.stopped_reason, StopReason) else str(self.stopped_reason),
        }


class Brain:
    """ReAct reasoning loop per architecture spec section 5.2.

    Each turn:
      1. Load memory context (Core + Recall + Archival) into system prompt
      2. Send messages + tool definitions to LLM
      3. If LLM returns tool_calls -> execute -> append observation -> loop
      4. If LLM returns text -> final response
    Terminates on: final text, max_steps (20), consecutive_errors (3), or budget.
    """

    MAX_STEPS = 20
    MAX_CONSECUTIVE_ERRORS = 3

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        llm_router: LLMRouter | None = None,
        cost_controller: CostController | None = None,
        max_steps: int = 20,
        core_memory: Any | None = None,
        recall_memory: Any | None = None,
        archival_memory: Any | None = None,
        workspace_memory: Any | None = None,
        memory_recent_limit: int = 8,
        evolution_engine: Any | None = None,
        correction_detector: Any | None = None,
        prompt_builder: PromptContractBuilder,
        hooks: HookRegistry | None = None,
        compaction: CompactionEngine | None = None,
        promotion: Any | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._llm_router = llm_router
        self._cost_controller = cost_controller
        self._max_steps = max(1, min(int(max_steps), self.MAX_STEPS))
        self._core_memory = core_memory
        self._recall_memory = recall_memory
        self._archival_memory = archival_memory
        self._workspace_memory = workspace_memory
        self._memory_recent_limit = max(1, min(int(memory_recent_limit), 50))
        self._evolution_engine = evolution_engine
        self._correction_detector = correction_detector
        self._prompt_builder = prompt_builder
        self._hooks = hooks or HookRegistry()
        self._compaction = compaction or CompactionEngine()
        self._promotion = promotion
        self._promotion_counters: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        query: str,
        ctx: TaskContext,
        metadata: dict[str, Any] | None = None,
        max_steps: int | None = None,
        prefer_llm: bool = True,
    ) -> BrainRunResult:
        safe_query = str(query or "").strip()
        if not safe_query:
            result = BrainRunResult(answer="Empty query.", used_tools=[], steps=[], stopped_reason=StopReason.empty_query)
            self._hooks.fire(
                HookPoint.on_task_end,
                ctx,
                {"stopped_reason": result.stopped_reason, "used_tools": [], "step_count": 0},
            )
            return result

        ctx.start_clock()

        safe_metadata = dict(metadata or {})
        safe_metadata["channel"] = ctx.extra.get("channel", safe_metadata.get("channel"))
        safe_metadata["user_id"] = ctx.extra.get("user_id", safe_metadata.get("user_id"))
        ctx.extra.update({
            "channel": safe_metadata.get("channel"),
            "user_id": safe_metadata.get("user_id"),
            "intent": safe_metadata.get("intent"),
            "route_hint": safe_metadata.get("route_hint"),
        })

        budget_steps = max(1, min(int(max_steps or self._max_steps), self.MAX_STEPS))

        explicit = self._parse_explicit_tool(safe_query)
        if explicit is not None:
            return await self._run_explicit_tool(explicit, safe_query, ctx)

        route_hint_explicit = self._route_hint_tool_call(safe_query, safe_metadata)

        if self._llm_router is None or not prefer_llm:
            if route_hint_explicit is not None:
                return await self._run_explicit_tool(route_hint_explicit, safe_query, ctx)
            return self._finalize_result(ctx, self._fallback_no_llm(safe_query, safe_metadata))

        if not self._llm_available():
            if route_hint_explicit is not None:
                return await self._run_explicit_tool(route_hint_explicit, safe_query, ctx)
            return self._finalize_result(ctx, self._fallback_no_llm(safe_query, safe_metadata))

        return await self._react_loop(query=safe_query, ctx=ctx, metadata=safe_metadata, budget_steps=budget_steps)

    # ------------------------------------------------------------------
    # Core ReAct loop
    # ------------------------------------------------------------------

    async def _react_loop(
        self,
        *,
        query: str,
        ctx: TaskContext,
        metadata: dict[str, Any],
        budget_steps: int,
    ) -> BrainRunResult:
        assert self._llm_router is not None

        # Hook: beforeTaskStart — 可阻断
        hook_result = self._hooks.fire(
            HookPoint.before_task_start, ctx,
            {"query": query},
        )
        if hook_result.block:
            return self._finalize_result(
                ctx,
                BrainRunResult(
                    answer=f"Task blocked: {hook_result.reason}",
                    used_tools=[], steps=[], stopped_reason=StopReason.task_blocked,
                ),
            )

        system_prompt = self._build_system_prompt(ctx=ctx, query=query)
        tool_defs, alias_map = self._build_tool_definitions()

        messages: list[Any] = [SystemMessage(content=system_prompt)]

        route_hint = metadata.get("route_hint")
        if isinstance(route_hint, dict):
            target = str(route_hint.get("target") or "").strip()
            intent = str(route_hint.get("intent") or "").strip()
            if target:
                hint = (
                    f"The intent router detected intent '{intent}' targeting module '{target}'. "
                    f"Consider using tool 'module_{target}' if the request aligns."
                )
                messages.append(SystemMessage(content=hint))

        messages.append(HumanMessage(content=query))

        steps: list[BrainStep] = []
        used_tools: list[str] = []
        consecutive_errors = 0
        stopped_reason = StopReason.max_steps

        for idx in range(budget_steps):
            if ctx.over_budget:
                stopped_reason = StopReason.budget_exhausted
                break
            if not self._reserve_cost(route="brain:react", query=query, tool_args={}, ctx=ctx):
                stopped_reason = StopReason.budget_exhausted
                break

            llm_route = "planning"
            if self._cost_controller is not None:
                llm_route = self._cost_controller.recommend_route("planning")

            try:
                ai_msg = await asyncio.to_thread(
                    self._llm_router.invoke_chat,
                    messages,
                    tools=tool_defs or None,
                    route=llm_route,
                )
            except Exception as exc:
                steps.append(BrainStep(
                    index=idx, thought=f"LLM error: {str(exc)[:200]}", action="error",
                    tool_name=None, tool_args={}, observation=str(exc)[:300],
                ))
                self._hooks.fire(
                    HookPoint.on_recovery,
                    ctx,
                    {"source": "brain", "error": str(exc)[:300], "recovery_level": "abort"},
                )
                stopped_reason = StopReason.llm_error
                break

            if not isinstance(ai_msg, AIMessage) or not ai_msg.tool_calls:
                content = ""
                if isinstance(ai_msg, AIMessage):
                    content = _coerce_text(ai_msg.content)
                else:
                    content = str(ai_msg)

                answer = self._apply_soul_style(content.strip() or self._summarize_steps(steps) or "任务已完成。")
                steps.append(BrainStep(
                    index=idx, thought="final response", action="respond",
                    tool_name=None, tool_args={}, observation=answer,
                ))
                stopped_reason = StopReason.completed
                self._remember_interaction(
                    query=query, answer=answer, ctx=ctx,
                    used_tools=used_tools, stopped_reason=stopped_reason, steps=steps,
                )
                self._run_compaction(ctx, steps)
                return self._finalize_result(
                    ctx,
                    BrainRunResult(answer=answer, used_tools=used_tools, steps=steps, stopped_reason=stopped_reason),
                )

            messages.append(ai_msg)

            for tc in ai_msg.tool_calls:
                tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                tc_raw_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                sanitized = str(tc_name or "").strip()
                original = alias_map.get(sanitized, sanitized)
                args = dict(tc_args or {})
                tc_id = str(tc_raw_id or f"call_{uuid.uuid4().hex[:10]}")

                if ctx.over_budget or not self._reserve_cost(route=f"tool:{original}", query=query, tool_args=args, ctx=ctx):
                    obs: Any = {"error": "Budget exceeded for this tool call"}
                    messages.append(ToolMessage(content=_serialize(obs), tool_call_id=tc_id))
                    steps.append(BrainStep(
                        index=idx, thought="budget check failed", action="use_tool",
                        tool_name=original, tool_args=args, observation=obs,
                    ))
                    stopped_reason = StopReason.budget_exhausted
                    continue

                # Hook: beforeToolUse — 可阻断
                tool_hook = self._hooks.fire(
                    HookPoint.before_tool_use, ctx,
                    {"tool_name": original, "tool_args": args},
                )
                if tool_hook.block:
                    obs = {"error": f"Tool blocked by hook: {tool_hook.reason}"}
                    messages.append(ToolMessage(content=_serialize(obs), tool_call_id=tc_id))
                    steps.append(BrainStep(
                        index=idx, thought=f"hook blocked {original}", action="use_tool",
                        tool_name=original, tool_args=args, observation=obs,
                    ))
                    stopped_reason = StopReason.tool_blocked
                    continue

                started = time.perf_counter()
                status = "ok"
                try:
                    obs = await self._tool_registry.invoke(original, args)
                    consecutive_errors = 0
                except Exception as exc:
                    obs = {"error": str(exc)[:500], "tool_name": original}
                    consecutive_errors += 1
                    status = "error"
                latency = int((time.perf_counter() - started) * 1000)

                # Hook: afterToolUse — 只观测
                self._hooks.fire(
                    HookPoint.after_tool_use, ctx,
                    {"tool_name": original, "tool_args": args, "observation": obs,
                     "status": status, "latency_ms": latency},
                )

                messages.append(ToolMessage(content=_serialize(obs), tool_call_id=tc_id))
                used_tools.append(original)
                steps.append(BrainStep(
                    index=idx, thought=f"called {original}", action="use_tool",
                    tool_name=original, tool_args=args, observation=obs,
                ))

                self._record_tool_call(
                    ctx=ctx, tool_name=original, tool_args=args,
                    observation=obs, status=status, latency_ms=latency,
                )

            if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                self._hooks.fire(
                    HookPoint.on_recovery,
                    ctx,
                    {"source": "brain", "recovery_level": "abort", "reason": "consecutive_tool_errors"},
                )
                stopped_reason = StopReason.error_aborted
                break

        fallback = self._apply_soul_style(self._summarize_steps(steps) or "已达到最大推理步数。")
        result = BrainRunResult(answer=fallback, used_tools=used_tools, steps=steps, stopped_reason=stopped_reason)
        self._remember_interaction(
            query=query, answer=fallback, ctx=ctx,
            used_tools=used_tools, stopped_reason=stopped_reason, steps=steps,
        )
        self._run_compaction(ctx, steps)
        return self._finalize_result(ctx, result)

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, *, ctx: TaskContext, query: str) -> str:
        """通过 PromptContractBuilder 组装 system prompt。"""
        contract = self._prompt_builder.build(ctx, query)
        prompt = contract.text
        ctx.consume_tokens(contract.token_estimate)
        if len(prompt) > 6000:
            prompt = prompt[:6000] + "\n...(context truncated)"
        return prompt

    # ------------------------------------------------------------------
    # Tool definitions for LLM
    # ------------------------------------------------------------------

    def _build_tool_definitions(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        specs = self._tool_registry.list_tools()
        if not specs:
            return [], {}
        defs: list[dict[str, Any]] = []
        alias_map: dict[str, str] = {}
        for spec in specs:
            sanitized = _sanitize_tool_name(spec.name)
            alias_map[sanitized] = spec.name
            defs.append({
                "type": "function",
                "function": {
                    "name": sanitized,
                    "description": str(spec.description or spec.name)[:512],
                    "parameters": _normalize_schema(spec.schema),
                },
            })
        return defs, alias_map

    # ------------------------------------------------------------------
    # Explicit /tool command (kept for backward compat & testing)
    # ------------------------------------------------------------------

    def _parse_explicit_tool(self, query: str) -> tuple[str, dict[str, Any]] | None:
        safe = str(query or "").strip()
        if not safe.lower().startswith("/tool "):
            return None
        body = safe[6:].strip()
        if not body:
            return None
        parts = body.split(maxsplit=1)
        tool_name = parts[0].strip()
        if not tool_name or self._tool_registry.get(tool_name) is None:
            return None
        raw_args = parts[1].strip() if len(parts) > 1 else ""
        if raw_args.startswith("{") and raw_args.endswith("}"):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return tool_name, parsed
            except (json.JSONDecodeError, ValueError):
                logger.debug("Failed to parse tool args as JSON: %s", raw_args[:100])
        if raw_args:
            return tool_name, {"query": raw_args, "text": raw_args}
        return tool_name, {}

    async def _run_explicit_tool(
        self,
        explicit: tuple[str, dict[str, Any]],
        query: str,
        ctx: TaskContext,
    ) -> BrainRunResult:
        tool_name, tool_args = explicit
        started = time.perf_counter()
        try:
            observation = await self._tool_registry.invoke(tool_name, tool_args)
            status = "ok"
        except Exception as exc:
            observation = {"error": str(exc)[:500]}
            status = "error"
        latency = int((time.perf_counter() - started) * 1000)

        self._record_tool_call(
            ctx=ctx, tool_name=tool_name, tool_args=tool_args,
            observation=observation, status=status, latency_ms=latency,
        )
        step = BrainStep(
            index=0, thought="explicit /tool command", action="use_tool",
            tool_name=tool_name, tool_args=tool_args, observation=observation,
        )
        answer = self._apply_soul_style(self._summarize_steps([step]) or "工具已执行。")
        result = BrainRunResult(answer=answer, used_tools=[tool_name], steps=[step], stopped_reason=StopReason.completed)
        self._remember_interaction(
            query=query, answer=answer, ctx=ctx,
            used_tools=[tool_name], stopped_reason=StopReason.completed, steps=[step],
        )
        self._run_compaction(ctx, [step])
        return self._finalize_result(ctx, result)

    def _fallback_no_llm(self, query: str, metadata: dict[str, Any]) -> BrainRunResult:
        msg = (
            "LLM is not configured. Use '/tool <name> <args>' to call tools directly.\n"
            "Available tools: " + ", ".join(s.name for s in self._tool_registry.list_tools()[:20])
        )
        return BrainRunResult(answer=msg, used_tools=[], steps=[], stopped_reason=StopReason.no_llm)

    def _route_hint_tool_call(
        self,
        query: str,
        metadata: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        route_hint = metadata.get("route_hint")
        if not isinstance(route_hint, dict):
            return None
        tool_name = str(route_hint.get("tool_name") or "").strip()
        if not tool_name or self._tool_registry.get(tool_name) is None:
            return None
        intent = str(route_hint.get("intent") or f"module.{tool_name.split('.')[-1]}").strip()
        return (
            tool_name,
            {
                "intent": intent,
                "text": query,
                "metadata": metadata,
            },
        )

    def _llm_available(self) -> bool:
        if self._llm_router is None:
            return False
        resolver = getattr(self._llm_router, "resolve_api_config", None)
        if not callable(resolver):
            return True
        try:
            resolver()
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.warning("LLM resolver check failed: %s", exc)
            return False
        return True

    def _finalize_result(self, ctx: TaskContext, result: BrainRunResult) -> BrainRunResult:
        self._hooks.fire(
            HookPoint.on_task_end,
            ctx,
            {
                "stopped_reason": result.stopped_reason.value if isinstance(result.stopped_reason, StopReason) else str(result.stopped_reason),
                "used_tools": list(result.used_tools),
                "step_count": len(result.steps),
            },
        )
        return result

    # ------------------------------------------------------------------
    # Cost control
    # ------------------------------------------------------------------

    def _reserve_cost(self, *, route: str, query: str, tool_args: dict[str, Any], ctx: TaskContext | None = None) -> bool:
        text = json.dumps(tool_args, ensure_ascii=False)
        tokens = self._cost_controller.estimate_tokens(query, text) if self._cost_controller is not None else max(1, len(query) // 4 + len(text) // 4)
        if ctx is not None:
            ctx.consume_tokens(tokens)
            if ctx.over_budget:
                self._hooks.fire(
                    HookPoint.on_recovery, ctx,
                    {"source": "budget", "recovery_level": "abort", "reason": "task_token_budget_exhausted"},
                )
                return False
            ratio = ctx.tokens_used / max(1, ctx.token_budget)
            if ratio >= 0.8 and not ctx.extra.get("_budget_warning_fired"):
                ctx.extra["_budget_warning_fired"] = True
                self._hooks.fire(
                    HookPoint.on_recovery, ctx,
                    {"source": "budget", "recovery_level": "degrade",
                     "reason": f"task_budget_80pct (used={ctx.tokens_used}/{ctx.token_budget})"},
                )
        if self._cost_controller is None:
            return True
        return self._cost_controller.reserve(route=route, tokens=tokens)

    # ------------------------------------------------------------------
    # Soul styling
    # ------------------------------------------------------------------

    def _apply_soul_style(self, answer: str) -> str:
        text = str(answer or "").strip()
        if not text:
            return text
        if self._core_memory is not None:
            try:
                soul = self._core_memory.read_block("soul")
            except (KeyError, TypeError, RuntimeError) as exc:
                logger.debug("Failed to read soul block: %s", exc)
                soul = None
            if isinstance(soul, dict):
                prefix = str(soul.get("assistant_prefix") or "").strip()
                if prefix and not text.startswith(prefix):
                    text = f"{prefix}: {text}"
        if len(text) > 2000:
            return text[:2000] + "...(truncated)"
        return text

    # ------------------------------------------------------------------
    # Memory write-back
    # ------------------------------------------------------------------

    def _remember_interaction(
        self,
        *,
        query: str,
        answer: str,
        ctx: TaskContext,
        used_tools: list[str],
        stopped_reason: StopReason,
        steps: list[BrainStep],
    ) -> None:
        if self._recall_memory is None:
            return
        session_id = ctx.session_id or "default"
        record_metadata = {
            "session_id": session_id,
            "channel": ctx.extra.get("channel"),
            "user_id": ctx.extra.get("user_id"),
            "used_tools": list(used_tools),
            "stopped_reason": stopped_reason.value if isinstance(stopped_reason, StopReason) else str(stopped_reason),
            "trace_id": ctx.trace_id,
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
        }

        prev_assistant = ""
        if self._correction_detector is not None:
            recent_before = self._recall_memory.recent(limit=3, session_id=session_id)
            for entry in reversed(recent_before):
                if str(entry.get("role") or "") == "assistant":
                    prev_assistant = str(entry.get("text") or "")
                    break

        self._recall_memory.add_interaction(
            user_text=query,
            assistant_text=answer,
            metadata=record_metadata,
            session_id=session_id,
            task_id=ctx.task_id or None,
            run_id=ctx.run_id or None,
            workspace_id=ctx.workspace_id,
        )

        if self._evolution_engine is not None:
            evolution_result = self._evolution_engine.reflect_interaction(
                user_text=query,
                assistant_text=answer,
                metadata=record_metadata,
            )
            record_metadata["evolution"] = evolution_result.to_dict()

        if self._correction_detector is not None and prev_assistant:
            self._correction_detector.check(
                user_text=query,
                previous_assistant_text=prev_assistant,
                metadata=record_metadata,
            )

    def _run_compaction(self, ctx: TaskContext, steps: list[BrainStep]) -> None:
        """每轮结束后执行 turn → taskRun 压缩，并根据 envelope.layer 路由写入。"""
        if not steps:
            return
        raw = [asdict(s) for s in steps]

        self._hooks.fire(HookPoint.before_compact, ctx, {"step_count": len(steps)})

        output = self._compaction.compact_turn(ctx, raw)
        envelope = self._compaction.to_envelope(ctx, output)

        self._route_envelope(envelope)

        self._hooks.fire(
            HookPoint.after_compact, ctx,
            {"summary": output.summary, "token_estimate": output.token_estimate},
        )

        self._run_promotion(ctx)

    def _route_envelope(self, envelope: Any) -> None:
        """根据 envelope.layer 将记忆写入对应存储层。

        Layer×Scope 存储路由核心 (§8.2):
          - operational → OperationalMemory (纯内存，不持久化)
          - recall      → RecallMemory.store_envelope
          - workspace   → WorkspaceMemory.set_summary
          - archival    → ArchivalMemory.store_envelope
          - core        → CoreMemory.update_block
          - meta        → 审计日志 (当前 fallback 到 recall)
        """
        layer = getattr(envelope, "layer", None)
        if layer == MemoryLayer.operational:
            logger.debug("Operational envelope %s — ephemeral, not persisted", getattr(envelope, "memory_id", "?"))
        elif layer == MemoryLayer.recall:
            if self._recall_memory is not None:
                self._recall_memory.store_envelope(envelope)
        elif layer == MemoryLayer.archival:
            if self._archival_memory is not None:
                self._archival_memory.store_envelope(envelope)
        elif layer == MemoryLayer.workspace:
            if self._workspace_memory is not None:
                ws_id = getattr(envelope, "workspace_id", None) or "default"
                content = getattr(envelope, "content", {})
                summary = content.get("summary", "") if isinstance(content, dict) else str(content)
                self._workspace_memory.set_summary(ws_id, summary)
        elif layer == MemoryLayer.core:
            if self._core_memory is not None:
                content = getattr(envelope, "content", {})
                block = content.get("predicate", "context") if isinstance(content, dict) else "context"
                value = content.get("object", "") if isinstance(content, dict) else str(content)
                try:
                    self._core_memory.update_block(block, value)
                except (AttributeError, TypeError) as exc:
                    logger.warning("Core memory write failed: %s", exc)
        elif layer == MemoryLayer.meta:
            if self._recall_memory is not None:
                self._recall_memory.store_envelope(envelope)
            logger.debug("Meta envelope %s stored to recall as audit trail", getattr(envelope, "memory_id", "?"))
        else:
            if self._recall_memory is not None:
                self._recall_memory.store_envelope(envelope)
            logger.debug("Unknown layer %s, fell back to recall", layer)

    def _run_promotion(self, ctx: TaskContext) -> None:
        """从 recall 中提取候选事实并晋升到 archival。

        节流: 同一 session 内每 5 轮才触发一次。
        """
        if self._promotion is None or self._recall_memory is None:
            return

        key = ctx.session_id or "default"
        count = self._promotion_counters.get(key, 0) + 1
        self._promotion_counters[key] = count
        if count % 5 != 0:
            return

        recent = self._recall_memory.recent(
            limit=20, session_id=key,
        )
        if recent:
            self._promotion.promote(ctx, recent)

    def _record_tool_call(
        self,
        *,
        ctx: TaskContext,
        tool_name: str,
        tool_args: dict[str, Any],
        observation: Any,
        status: str,
        latency_ms: int,
    ) -> None:
        if self._recall_memory is None:
            return
        self._recall_memory.record_tool_call(
            session_id=ctx.session_id or "default",
            task_id=ctx.task_id or None,
            run_id=ctx.run_id or None,
            workspace_id=ctx.workspace_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=observation,
            status=status,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_steps(self, steps: list[BrainStep]) -> str:
        tool_steps = [s for s in steps if s.action == "use_tool" and s.tool_name]
        if not tool_steps:
            return ""
        lines = ["已完成工具链执行："]
        for s in tool_steps:
            lines.append(f"- {s.tool_name}: {_short_observation(s.observation)}")
        text = "\n".join(lines)
        return text[:480] + "...(truncated)" if len(text) > 480 else text

    @staticmethod
    def _render_observation(*, tool_name: str, observation: Any) -> str:
        body = _serialize(observation).strip()
        if len(body) > 2000:
            body = body[:2000] + "...(truncated)"
        return f"[{tool_name}] {body}"


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _coerce_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content)


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _short_observation(observation: Any) -> str:
    if isinstance(observation, dict):
        if "error" in observation:
            return f"error={str(observation.get('error') or '')[:120]}"
        try:
            text = json.dumps(observation, ensure_ascii=False)
        except Exception:
            text = str(observation)
        return text[:200] + ("..." if len(text) > 200 else "")
    if isinstance(observation, list):
        return f"list(len={len(observation)})"
    text = str(observation or "").strip()
    return text[:200] + ("..." if len(text) > 200 else "")
