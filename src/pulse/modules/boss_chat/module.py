from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...core.llm.router import LLMRouter
from ...integrations.boss import build_boss_platform_connector
from ...core.module import BaseModule
from ...core.notify.notifier import ConsoleNotifier, Notification
from ...core.task_context import TaskContext

logger = logging.getLogger(__name__)


class BossChatProcessRequest(BaseModel):
    max_conversations: int = Field(default=20, ge=1, le=100)
    unread_only: bool = True
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    notify_on_escalate: bool = True
    fetch_latest_hr: bool = True
    auto_execute: bool = False
    chat_tab: str = Field(default="未读", max_length=30)
    confirm_execute: bool = False


class BossChatPullRequest(BaseModel):
    max_conversations: int = Field(default=20, ge=1, le=100)
    unread_only: bool = False
    fetch_latest_hr: bool = True
    chat_tab: str = Field(default="全部", max_length=30)


class BossChatExecuteRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)
    action: str = Field(default="reply_from_profile", max_length=40)
    reply_text: str | None = Field(default=None, max_length=2000)
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    run_id: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=400)
    conversation_hint: dict[str, Any] | None = None
    confirm_execute: bool = False


class BossChatIngestItem(BaseModel):
    hr_name: str = Field(..., min_length=1, max_length=80)
    company: str = Field(..., min_length=1, max_length=120)
    job_title: str = Field(..., min_length=1, max_length=160)
    latest_message: str = Field(..., min_length=1, max_length=2000)
    latest_time: str | None = Field(default=None, max_length=40)
    unread_count: int = Field(default=1, ge=0, le=99)
    conversation_id: str | None = Field(default=None, max_length=64)


class BossChatIngestRequest(BaseModel):
    items: list[BossChatIngestItem] = Field(default_factory=list, max_length=200)
    source: str = Field(default="manual", max_length=40)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_path(raw_path: str | None, *, default_path: Path) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return default_path
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _safe_bool(value: str | None, *, default: bool) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _safe_action(message: str) -> tuple[str, str, str | None]:
    lowered = message.lower()
    if any(token in lowered for token in ("简历", "resume", "作品集", "附件")):
        return "send_resume", "HR 明确要求简历材料", "您好，已收到，我这边马上发送简历。"
    if any(token in lowered for token in ("到岗", "方便", "时间", "技术栈", "python", "rag")):
        return "reply_from_profile", "问题可依据标准画像直接回复", "你好，我熟悉 Python/RAG，可一周内到岗，支持线下面试。"
    if any(token in lowered for token in ("电话", "线下", "薪资", "offer")):
        return "notify_user", "涉及沟通策略，建议用户确认后再回复", None
    return "ignore", "低优先级消息，不触发自动处理", None


class BossChatModule(BaseModule):
    name = "boss_chat"
    description = "Phase1 chat copilot module"
    route_prefix = "/api/modules/boss_chat"
    tags = ["boss_chat"]

    def __init__(self) -> None:
        super().__init__()
        self._notifier = ConsoleNotifier()
        self._connector = build_boss_platform_connector()
        self._llm_router = LLMRouter()
        self._hitl_required = _safe_bool(os.getenv("PULSE_BOSS_HITL_REQUIRED", "true"), default=True)
        self._allow_local_inbox_fallback = _safe_bool(
            os.getenv("PULSE_BOSS_ALLOW_LOCAL_INBOX_FALLBACK", "false"),
            default=False,
        )
        self._inbox_path = _resolve_path(
            os.getenv("PULSE_BOSS_CHAT_INBOX_PATH", ""),
            default_path=Path.home() / ".pulse" / "boss_chat_inbox.jsonl",
        )

    # -- AgentRuntime integration ------------------------------------------

    def on_startup(self) -> None:
        if not self._runtime:
            return
        if not _safe_bool(os.getenv("GUARD_CHAT_ENABLED"), default=False):
            return
        self._runtime.register_patrol(
            name="boss_chat.patrol",
            handler=self._patrol,
            peak_interval=int(os.getenv("GUARD_CHAT_INTERVAL_PEAK", "180")),
            offpeak_interval=int(os.getenv("GUARD_CHAT_INTERVAL_OFFPEAK", "600")),
        )

    def _patrol(self, ctx: TaskContext) -> dict[str, Any]:
        """Complete Agent Turn for boss_chat: pull → classify → reply."""
        auto_execute = _safe_bool(
            os.getenv("BOSS_CHAT_AUTO_EXECUTE_ENABLED"), default=False,
        )
        return self.run_process(
            max_conversations=20,
            unread_only=True,
            profile_id="default",
            notify_on_escalate=True,
            fetch_latest_hr=True,
            auto_execute=auto_execute,
            chat_tab="未读",
            confirm_execute=not self._hitl_required,
        )

    def _plan_action(self, message: str) -> tuple[str, str, str | None]:
        safe_message = str(message or "").strip()
        if not safe_message:
            return "ignore", "empty message", None
        prompt = (
            "You classify HR chat messages for an AI job assistant. "
            "Return ONLY valid JSON with keys: "
            "{\"action\":\"send_resume|reply_from_profile|notify_user|ignore\","
            "\"reason\":\"...\",\"reply_text\":\"... or empty\"}\n\n"
            f"Latest HR message: {safe_message[:1200]}"
        )
        try:
            raw = self._llm_router.invoke_text(prompt, route="classification")
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            parsed = json.loads(cleaned)
            action = str(parsed.get("action") or "").strip().lower()
            reason = str(parsed.get("reason") or "").strip()
            reply_text = str(parsed.get("reply_text") or "").strip() or None
            if action in {"send_resume", "reply_from_profile", "notify_user", "ignore"}:
                return action, reason or "llm_classification", reply_text
        except Exception as exc:
            logger.warning("boss_chat llm planning failed, fallback to heuristic: %s", exc)
        return _safe_action(safe_message)

    @staticmethod
    def _normalize_conversation(row: dict[str, Any]) -> dict[str, Any] | None:
        hr_name = str(row.get("hr_name") or "").strip()
        company = str(row.get("company") or "").strip()
        job_title = str(row.get("job_title") or "").strip()
        latest_message = str(row.get("latest_message") or row.get("latest_hr_message") or "").strip()
        if not hr_name or not company or not job_title or not latest_message:
            return None
        conversation_id = str(row.get("conversation_id") or "").strip()
        if not conversation_id:
            seed = f"{company}-{job_title}-{hr_name}"
            conversation_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return {
            "conversation_id": conversation_id,
            "hr_name": hr_name,
            "company": company,
            "job_title": job_title,
            "latest_message": latest_message,
            "latest_time": str(row.get("latest_time") or row.get("latest_hr_time") or "刚刚"),
            "unread_count": max(0, min(int(row.get("unread_count") or 0), 99)),
        }

    def _load_local_inbox(
        self,
        *,
        max_conversations: int,
        unread_only: bool,
        chat_tab: str,
    ) -> list[dict[str, Any]]:
        path = self._inbox_path
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_conversation(item)
            if normalized is not None:
                rows.append(normalized)
        if not rows:
            return []
        force_unread = chat_tab in {"未读", "新招呼"}
        if unread_only or force_unread:
            rows = [row for row in rows if int(row.get("unread_count") or 0) > 0]
        rows.reverse()
        return rows[: max(1, min(max_conversations, 100))]

    def _load_inbox(
        self,
        *,
        max_conversations: int,
        unread_only: bool,
        fetch_latest_hr: bool,
        chat_tab: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="inbox_load",
            status="started",
            trace_id=trace_id,
            payload={
                "max_conversations": max_conversations,
                "unread_only": unread_only,
                "fetch_latest_hr": fetch_latest_hr,
                "chat_tab": chat_tab,
            },
        )
        provider_errors: list[str] = []
        if self._connector.execution_ready:
            provider_result = self._connector.pull_conversations(
                max_conversations=max_conversations,
                unread_only=unread_only,
                fetch_latest_hr=fetch_latest_hr,
                chat_tab=chat_tab,
            )
            provider_errors.extend([str(item)[:400] for item in list(provider_result.get("errors") or [])])
            provider_items = list(provider_result.get("items") or [])
            normalized: list[dict[str, Any]] = []
            for row in provider_items:
                if not isinstance(row, dict):
                    continue
                item = self._normalize_conversation(row)
                if item is not None:
                    normalized.append(item)
            if normalized:
                force_unread = chat_tab in {"未读", "新招呼"}
                if unread_only or force_unread:
                    normalized = [row for row in normalized if int(row.get("unread_count") or 0) > 0]
                result = {
                    "items": normalized[: max(1, min(max_conversations, 100))],
                    "source": str(provider_result.get("source") or self._connector.provider_name),
                    "errors": provider_errors,
                }
                self.emit_stage_event(
                    stage="inbox_load",
                    status="completed",
                    trace_id=trace_id,
                    payload={
                        "source": result["source"],
                        "total": len(result["items"]),
                        "errors_total": len(result["errors"]),
                    },
                )
                return result
        else:
            provider_errors.append("provider is not execution-ready")
        if not self._allow_local_inbox_fallback:
            provider_errors.append("local inbox fallback is disabled")
            result = {
                "items": [],
                "source": self._connector.provider_name,
                "errors": provider_errors,
            }
            self.emit_stage_event(
                stage="inbox_load",
                status="failed",
                trace_id=trace_id,
                payload={
                    "source": result["source"],
                    "total": 0,
                    "errors_total": len(result["errors"]),
                },
            )
            return result
        local_items = self._load_local_inbox(
            max_conversations=max_conversations,
            unread_only=unread_only,
            chat_tab=chat_tab,
        )
        local_source = "local_inbox_jsonl" if self._inbox_path.is_file() else "local_inbox_empty"
        result = {
            "items": local_items,
            "source": local_source,
            "errors": provider_errors,
        }
        self.emit_stage_event(
            stage="inbox_load",
            status="completed",
            trace_id=trace_id,
            payload={
                "source": result["source"],
                "total": len(result["items"]),
                "errors_total": len(result["errors"]),
            },
        )
        return result

    def run_ingest(self, *, rows: list[BossChatIngestItem], source: str, trace_id: str | None = None) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="ingest",
            status="started",
            trace_id=trace_id,
            payload={
                "source": source,
                "rows_total": len(rows),
            },
        )
        if not rows:
            result = {"ok": False, "trace_id": trace_id, "inserted": 0}
            self.emit_stage_event(
                stage="ingest",
                status="failed",
                trace_id=trace_id,
                payload={"source": source, "rows_total": 0, "inserted": 0},
            )
            return result
        now_iso = datetime.now(timezone.utc).isoformat()
        prepared_rows: list[dict[str, Any]] = []
        for row in rows:
            payload = row.model_dump()
            if not payload.get("conversation_id"):
                seed = f"{payload['company']}-{payload['job_title']}-{payload['hr_name']}-{payload['latest_message']}"
                payload["conversation_id"] = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
            payload["source"] = str(source or "manual").strip() or "manual"
            payload["ingested_at"] = now_iso
            prepared_rows.append(payload)

        db_inserted = 0
        db_error = ""
        try:
            from ...core.storage.engine import DatabaseEngine
            import uuid as _uuid
            db = DatabaseEngine()
            for payload in prepared_rows:
                msg_sig = hashlib.sha1(
                    f"{payload['conversation_id']}-{payload.get('latest_message','')}".encode("utf-8")
                ).hexdigest()[:32]
                result = db.execute(
                    """INSERT INTO boss_chat_events(id, conversation_id, hr_name, company, job_title,
                       latest_hr_message, message_signature, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (message_signature) DO NOTHING
                       RETURNING id""",
                    (
                        _uuid.uuid4().hex,
                        payload.get("conversation_id") or "",
                        payload.get("hr_name") or "",
                        payload.get("company") or "",
                        payload.get("job_title") or "",
                        payload.get("latest_message") or "",
                        msg_sig,
                    ),
                    fetch="one",
                )
                if result is not None:
                    db_inserted += 1
        except Exception as exc:
            db_error = str(exc)[:400]
            if self._allow_local_inbox_fallback:
                logger.warning("boss_chat DB ingest failed, keep local inbox fallback enabled: %s", exc)
            else:
                logger.warning("boss_chat DB ingest failed and local fallback is disabled: %s", exc)

        local_written = 0
        local_error = ""
        if self._allow_local_inbox_fallback:
            try:
                self._inbox_path.parent.mkdir(parents=True, exist_ok=True)
                with self._inbox_path.open("a", encoding="utf-8") as handle:
                    for payload in prepared_rows:
                        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                        local_written += 1
            except Exception as exc:
                local_error = str(exc)[:400]
                logger.warning("boss_chat local inbox write failed: %s", exc)

        inserted = max(db_inserted, local_written)
        error = db_error or local_error or None
        if inserted > 0:
            error = None
        result = {
            "ok": inserted > 0,
            "trace_id": trace_id,
            "inserted": inserted,
            "db_inserted": db_inserted,
            "local_written": local_written,
            "local_fallback_enabled": self._allow_local_inbox_fallback,
            "error": error,
        }
        self.emit_stage_event(
            stage="ingest",
            status="completed" if result["ok"] else "failed",
            trace_id=trace_id,
            payload={
                "source": source,
                "inserted": inserted,
                "db_inserted": db_inserted,
                "local_written": local_written,
                "local_fallback_enabled": self._allow_local_inbox_fallback,
                "error": error,
            },
        )
        return result

    def run_process(
        self,
        *,
        max_conversations: int,
        unread_only: bool,
        profile_id: str,
        notify_on_escalate: bool,
        fetch_latest_hr: bool,
        auto_execute: bool,
        chat_tab: str,
        confirm_execute: bool,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="process",
            status="started",
            trace_id=trace_id,
            payload={
                "max_conversations": max_conversations,
                "unread_only": unread_only,
                "profile_id": profile_id,
                "notify_on_escalate": notify_on_escalate,
                "fetch_latest_hr": fetch_latest_hr,
                "auto_execute": auto_execute,
                "chat_tab": chat_tab,
                "confirm_execute": confirm_execute,
            },
        )
        inbox = self._load_inbox(
            max_conversations=max_conversations,
            unread_only=unread_only,
            fetch_latest_hr=fetch_latest_hr,
            chat_tab=chat_tab,
            trace_id=trace_id,
        )
        conversations = list(inbox.get("items") or [])
        errors = [str(item)[:400] for item in list(inbox.get("errors") or [])]
        source = str(inbox.get("source") or "unknown")
        items: list[dict[str, Any]] = []
        notify_count = 0
        for row in conversations:
            action, reason, reply_text = self._plan_action(str(row.get("latest_message") or ""))
            execution: dict[str, Any] | None = None
            if action == "notify_user" and notify_on_escalate:
                notify_count += 1
                self._notifier.send(
                    Notification(
                        level="warning",
                        title="boss_chat escalated",
                        content=f"{row.get('company')} / {row.get('job_title')}: {reason}",
                        metadata={"conversation_id": row.get("conversation_id")},
                    )
                )
            if auto_execute and action in {"send_resume", "reply_from_profile"}:
                if self._hitl_required and not confirm_execute:
                    execution = {
                        "ok": False,
                        "status": "pending_confirmation",
                        "needs_confirmation": True,
                        "error": "confirmation required before real execution",
                    }
                else:
                    reply = str(reply_text or "").strip()
                    if not reply:
                        execution = {
                            "ok": False,
                            "status": "failed",
                            "needs_confirmation": False,
                            "error": "reply text is empty",
                        }
                    else:
                        reply_result = self._connector.reply_conversation(
                            conversation_id=str(row.get("conversation_id") or ""),
                            reply_text=reply,
                            profile_id=profile_id,
                            conversation_hint={
                                "hr_name": str(row.get("hr_name") or ""),
                                "company": str(row.get("company") or ""),
                                "job_title": str(row.get("job_title") or ""),
                            },
                        )
                        mark_result = self._connector.mark_processed(
                            conversation_id=str(row.get("conversation_id") or ""),
                            run_id=datetime.now(timezone.utc).strftime("chat-%Y%m%d%H%M%S"),
                            note="auto_execute from boss_chat.process",
                        )
                        execution = {
                            "ok": bool(reply_result.get("ok")) and bool(mark_result.get("ok")),
                            "status": "sent" if bool(reply_result.get("ok")) and bool(mark_result.get("ok")) else "failed",
                            "needs_confirmation": False,
                            "reply_result": reply_result,
                            "mark_result": mark_result,
                        }
                        for candidate_error in (reply_result.get("error"), mark_result.get("error")):
                            error = str(candidate_error or "").strip()
                            if error:
                                errors.append(error[:400])
            items.append(
                {
                    "conversation_id": row["conversation_id"],
                    "hr_name": row["hr_name"],
                    "company": row["company"],
                    "job_title": row["job_title"],
                    "latest_hr_message": row["latest_message"],
                    "latest_hr_time": row["latest_time"],
                    "action": action,
                    "reason": reason,
                    "reply_text": reply_text,
                    "auto_executed": bool(execution and execution.get("ok")),
                    "execution": execution,
                }
            )
        result = {
            "trace_id": trace_id,
            "processed_count": len(items),
            "new_count": sum(1 for row in conversations if int(row.get("unread_count") or 0) > 0),
            "duplicated_count": 0,
            "screenshot_path": None,
            "notified_count": notify_count,
            "needs_confirmation": bool(
                auto_execute and self._hitl_required and not confirm_execute and any(
                    item.get("action") in {"send_resume", "reply_from_profile"} for item in items
                )
            ),
            "items": items,
            "summary": {
                "profile_id": profile_id,
                "chat_tab": chat_tab,
                "source": source,
                "inbox_path": str(self._inbox_path),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "errors": errors,
        }
        self.emit_stage_event(
            stage="process",
            status="completed",
            trace_id=trace_id,
            payload={
                "source": source,
                "processed_count": result["processed_count"],
                "new_count": result["new_count"],
                "notified_count": notify_count,
                "errors_total": len(errors),
            },
        )
        return result

    def run_pull(
        self,
        *,
        max_conversations: int,
        unread_only: bool,
        fetch_latest_hr: bool,
        chat_tab: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="pull",
            status="started",
            trace_id=trace_id,
            payload={
                "max_conversations": max_conversations,
                "unread_only": unread_only,
                "fetch_latest_hr": fetch_latest_hr,
                "chat_tab": chat_tab,
            },
        )
        inbox = self._load_inbox(
            max_conversations=max_conversations,
            unread_only=unread_only,
            fetch_latest_hr=fetch_latest_hr,
            chat_tab=chat_tab,
            trace_id=trace_id,
        )
        items = list(inbox.get("items") or [])
        unread_total = sum(int(item.get("unread_count") or 0) for item in items)
        result = {
            "trace_id": trace_id,
            "total": len(items),
            "unread_total": unread_total,
            "screenshot_path": None,
            "items": items,
            "inbox_path": str(self._inbox_path),
            "source": str(inbox.get("source") or "unknown"),
            "errors": [str(item)[:400] for item in list(inbox.get("errors") or [])],
        }
        self.emit_stage_event(
            stage="pull",
            status="completed",
            trace_id=trace_id,
            payload={
                "source": result["source"],
                "total": result["total"],
                "unread_total": unread_total,
                "errors_total": len(result["errors"]),
            },
        )
        return result

    def run_execute(
        self,
        *,
        conversation_id: str,
        action: str,
        reply_text: str | None,
        profile_id: str,
        run_id: str | None,
        note: str | None,
        conversation_hint: dict[str, Any] | None,
        confirm_execute: bool,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="execute",
            status="started",
            trace_id=trace_id,
            payload={
                "conversation_id": str(conversation_id or "").strip(),
                "action": str(action or "").strip().lower() or "reply_from_profile",
                "confirm_execute": confirm_execute,
            },
        )
        safe_conversation_id = str(conversation_id or "").strip()
        if not safe_conversation_id:
            result = {"ok": False, "trace_id": trace_id, "error": "conversation_id is required"}
            self.emit_stage_event(
                stage="execute",
                status="failed",
                trace_id=trace_id,
                payload={"conversation_id": safe_conversation_id, "error": result["error"]},
            )
            return result
        action_lower = str(action or "").strip().lower() or "reply_from_profile"
        if self._hitl_required and not confirm_execute:
            result = {
                "ok": True,
                "trace_id": trace_id,
                "needs_confirmation": True,
                "conversation_id": safe_conversation_id,
                "action": action_lower,
                "reason": "confirmation required before real execution",
            }
            self.emit_stage_event(
                stage="execute",
                status="preview",
                trace_id=trace_id,
                payload={
                    "conversation_id": safe_conversation_id,
                    "action": action_lower,
                },
            )
            return result
        if action_lower in {"reply", "reply_from_profile", "send_resume"}:
            safe_reply = str(reply_text or "").strip()
            if not safe_reply:
                result = {"ok": False, "trace_id": trace_id, "error": "reply_text is required for reply action"}
                self.emit_stage_event(
                    stage="execute",
                    status="failed",
                    trace_id=trace_id,
                    payload={
                        "conversation_id": safe_conversation_id,
                        "action": action_lower,
                        "error": result["error"],
                    },
                )
                return result
            reply_result = self._connector.reply_conversation(
                conversation_id=safe_conversation_id,
                reply_text=safe_reply,
                profile_id=profile_id,
                conversation_hint=dict(conversation_hint or {}),
            )
            mark_result = self._connector.mark_processed(
                conversation_id=safe_conversation_id,
                run_id=run_id or datetime.now(timezone.utc).strftime("chat-%Y%m%d%H%M%S"),
                note=note or f"execute action={action_lower}",
            )
            ok = bool(reply_result.get("ok")) and bool(mark_result.get("ok"))
            result = {
                "ok": ok,
                "trace_id": trace_id,
                "needs_confirmation": False,
                "conversation_id": safe_conversation_id,
                "action": action_lower,
                "status": "sent" if ok else "failed",
                "reply_result": reply_result,
                "mark_result": mark_result,
            }
            self.emit_stage_event(
                stage="execute",
                status="completed" if ok else "failed",
                trace_id=trace_id,
                payload={
                    "conversation_id": safe_conversation_id,
                    "action": action_lower,
                    "status": result["status"],
                },
            )
            return result
        if action_lower in {"mark_processed", "mark"}:
            mark_result = self._connector.mark_processed(
                conversation_id=safe_conversation_id,
                run_id=run_id or datetime.now(timezone.utc).strftime("chat-%Y%m%d%H%M%S"),
                note=note or "manual mark",
            )
            result = {
                "ok": bool(mark_result.get("ok")),
                "trace_id": trace_id,
                "needs_confirmation": False,
                "conversation_id": safe_conversation_id,
                "action": action_lower,
                "status": str(mark_result.get("status") or "failed"),
                "mark_result": mark_result,
            }
            self.emit_stage_event(
                stage="execute",
                status="completed" if result["ok"] else "failed",
                trace_id=trace_id,
                payload={
                    "conversation_id": safe_conversation_id,
                    "action": action_lower,
                    "status": result["status"],
                },
            )
            return result
        result = {
            "ok": False,
            "trace_id": trace_id,
            "needs_confirmation": False,
            "conversation_id": safe_conversation_id,
            "action": action_lower,
            "error": f"unsupported action={action_lower}",
        }
        self.emit_stage_event(
            stage="execute",
            status="failed",
            trace_id=trace_id,
            payload={
                "conversation_id": safe_conversation_id,
                "action": action_lower,
                "error": result["error"],
            },
        )
        return result

    def handle_intent(
        self,
        intent: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        trace_id = str((metadata or {}).get("trace_id") or "").strip() or None
        _ = text
        if intent == "boss.chat.pull":
            return self.run_pull(
                max_conversations=10,
                unread_only=True,
                fetch_latest_hr=True,
                chat_tab="未读",
                trace_id=trace_id,
            )
        if intent == "boss.chat.process":
            return self.run_process(
                max_conversations=10,
                unread_only=True,
                profile_id="default",
                notify_on_escalate=True,
                fetch_latest_hr=True,
                auto_execute=False,
                chat_tab="未读",
                confirm_execute=False,
                trace_id=trace_id,
            )
        return None

    def register_routes(self, router: APIRouter) -> None:
        @router.get("/health")
        async def health() -> dict[str, Any]:
            sample = self._load_inbox(
                max_conversations=1,
                unread_only=False,
                fetch_latest_hr=True,
                chat_tab="全部",
            )
            return {
                "module": self.name,
                "status": "ok",
                "runtime": {
                    "mode": "real_connector" if self._connector.execution_ready else "degraded_connector",
                    "provider": self._connector.provider_name,
                    "hitl_required": self._hitl_required,
                    "inbox_path": str(self._inbox_path),
                    "local_inbox_fallback_enabled": self._allow_local_inbox_fallback,
                    "available_conversations": len(list(sample.get("items") or [])),
                    "source": sample.get("source"),
                    "connector": self._connector.health(),
                },
            }

        @router.post("/inbox/ingest")
        async def inbox_ingest(payload: BossChatIngestRequest) -> dict[str, Any]:
            return self.run_ingest(rows=payload.items, source=payload.source)

        @router.post("/process")
        async def process(payload: BossChatProcessRequest) -> dict[str, Any]:
            return self.run_process(
                max_conversations=payload.max_conversations,
                unread_only=payload.unread_only,
                profile_id=payload.profile_id,
                notify_on_escalate=payload.notify_on_escalate,
                fetch_latest_hr=payload.fetch_latest_hr,
                auto_execute=payload.auto_execute,
                chat_tab=payload.chat_tab,
                confirm_execute=payload.confirm_execute,
            )

        @router.post("/pull")
        async def pull(payload: BossChatPullRequest) -> dict[str, Any]:
            return self.run_pull(
                max_conversations=payload.max_conversations,
                unread_only=payload.unread_only,
                fetch_latest_hr=payload.fetch_latest_hr,
                chat_tab=payload.chat_tab,
            )

        @router.post("/execute")
        async def execute(payload: BossChatExecuteRequest) -> dict[str, Any]:
            return self.run_execute(
                conversation_id=payload.conversation_id,
                action=payload.action,
                reply_text=payload.reply_text,
                profile_id=payload.profile_id,
                run_id=payload.run_id,
                note=payload.note,
                conversation_hint=payload.conversation_hint,
                confirm_execute=payload.confirm_execute,
            )

        @router.get("/session/check")
        async def session_check() -> dict[str, Any]:
            return self._connector.check_login()

def get_module() -> BossChatModule:
    return BossChatModule()
