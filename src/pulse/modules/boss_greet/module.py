from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...integrations.boss import build_boss_platform_connector
from ...core.module import BaseModule
from ...core.notify.notifier import ConsoleNotifier, Notification
from ...core.scheduler.windows import is_active_hour, is_weekend
from ...core.task_context import TaskContext


class BossScanRunRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=120)
    max_items: int = Field(default=10, ge=1, le=50)
    max_pages: int = Field(default=1, ge=1, le=10)
    job_type: str = Field(default="all", max_length=20)
    fetch_detail: bool = False


class BossGreetTriggerRequest(BaseModel):
    keyword: str = Field(default="AI Agent 实习", min_length=1, max_length=120)
    batch_size: int | None = Field(default=None, ge=1, le=20)
    match_threshold: float | None = Field(default=None, ge=30, le=95)
    greeting_text: str | None = Field(default=None, max_length=300)
    job_type: str = Field(default="all", max_length=20)
    run_id: str | None = Field(default=None, max_length=120)
    confirm_execute: bool = False
    fetch_detail: bool = True


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


def _safe_batch_size(value: int | None) -> int:
    if value is not None:
        return max(1, min(int(value), 20))
    raw = os.getenv("BOSS_GREET_BATCH_SIZE", "3").strip()
    try:
        return max(1, min(int(raw), 20))
    except ValueError:
        return 3


def _safe_threshold(value: float | None) -> float:
    if value is not None:
        return max(30.0, min(float(value), 95.0))
    raw = os.getenv("BOSS_GREET_MATCH_THRESHOLD", "65").strip()
    try:
        parsed = float(raw)
    except ValueError:
        parsed = 65.0
    return max(30.0, min(parsed, 95.0))


def _score_keyword_match(keyword: str, title: str, snippet: str) -> float:
    lowered = f"{title} {snippet}".lower()
    key = keyword.strip().lower()
    if not key:
        return 60.0
    score = 52.0
    if key in lowered:
        score += 28.0
    tokens = [token for token in key.replace("/", " ").replace("-", " ").split() if token]
    if tokens:
        hits = sum(1 for token in tokens if token in lowered)
        score += (hits / max(1, len(tokens))) * 20.0
    return round(max(35.0, min(score, 97.0)), 1)


def _guess_title(raw_title: str, *, keyword: str) -> str:
    title = re.sub(r"\s+", " ", str(raw_title or "").strip())
    if not title:
        return f"{keyword} 招聘信息"
    for sep in (" - ", " | ", " _ ", "｜", "|", "-", "_"):
        if sep in title:
            candidate = title.split(sep, 1)[0].strip()
            if len(candidate) >= 4:
                return candidate[:120]
    return title[:120]


def _guess_company(title: str, url: str) -> str:
    for sep in (" - ", " | ", " _ ", "｜", "|", "-", "_"):
        if sep in title:
            parts = [item.strip() for item in title.split(sep) if item.strip()]
            if len(parts) >= 2:
                return parts[1][:80]
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0].strip()
        if host:
            return host[:80]
    return "Unknown"


class BossGreetModule(BaseModule):
    name = "boss_greet"
    description = "Phase1 job scan and greet module"
    route_prefix = "/api/modules/boss_greet"
    tags = ["boss_greet"]

    def __init__(self) -> None:
        super().__init__()
        self._notifier = ConsoleNotifier()
        self._connector = build_boss_platform_connector()
        self._hitl_required = _safe_bool(os.getenv("PULSE_BOSS_HITL_REQUIRED", "true"), default=True)
        self._greet_log_path = _resolve_path(
            os.getenv("PULSE_BOSS_GREET_LOG_PATH", ""),
            default_path=Path.home() / ".pulse" / "boss_greet_log.jsonl",
        )

    # -- AgentRuntime integration ------------------------------------------

    def on_startup(self) -> None:
        if not self._runtime:
            return
        if not _safe_bool(os.getenv("GUARD_GREET_ENABLED"), default=False):
            return
        self._runtime.register_patrol(
            name="boss_greet.patrol",
            handler=self._patrol,
            peak_interval=int(os.getenv("GUARD_GREET_INTERVAL_PEAK", "900")),
            offpeak_interval=int(os.getenv("GUARD_GREET_INTERVAL_OFFPEAK", "1800")),
        )

    def _patrol(self, ctx: TaskContext) -> dict[str, Any]:
        """Complete Agent Turn for boss_greet: scan → match → greet."""
        keyword = os.getenv("GUARD_GREET_KEYWORD", "AI Agent 实习")
        return self.run_trigger(
            keyword=keyword,
            batch_size=int(os.getenv("BOSS_GREET_BATCH_SIZE", "3")),
            match_threshold=None,
            confirm_execute=not self._hitl_required,
            fetch_detail=True,
        )

    @staticmethod
    def _extract_keyword(text: str, *, fallback: str = "AI Agent 实习") -> str:
        raw = str(text or "").strip()
        lowered = raw.lower()
        for prefix in ("/scan", "/greet", "scan", "greet"):
            if lowered.startswith(prefix):
                candidate = raw[len(prefix) :].strip()
                if candidate:
                    return candidate
        return raw or fallback

    @staticmethod
    def _normalize_scan_item(keyword: str, row: dict[str, Any]) -> dict[str, Any]:
        source_url = str(row.get("source_url") or row.get("url") or "").strip()
        title_raw = str(row.get("title") or "").strip()
        dedupe_seed = (source_url or title_raw or json.dumps(row, ensure_ascii=False)[:120]).lower()
        title = _guess_title(title_raw, keyword=keyword)
        snippet = str(row.get("snippet") or row.get("description") or title_raw or "")[:1000]
        company_raw = str(row.get("company") or "").strip()
        company = company_raw if company_raw else _guess_company(title_raw, source_url)
        if not source_url:
            source_url = f"https://www.zhipin.com/job_detail/{hashlib.sha1(dedupe_seed.encode('utf-8')).hexdigest()[:16]}"
        job_id = str(row.get("job_id") or "").strip() or hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16]
        detail_raw = row.get("detail")
        detail = dict(detail_raw) if isinstance(detail_raw, dict) else {}
        return {
            "job_id": job_id,
            "title": title,
            "company": company,
            "salary": row.get("salary"),
            "source_url": source_url,
            "snippet": snippet,
            "detail": detail,
            "match_score": _score_keyword_match(keyword, title, snippet),
            "source": str(row.get("source") or "").strip() or "boss_unknown",
            "collected_at": str(row.get("collected_at") or datetime.now(timezone.utc).isoformat()),
        }

    def run_scan(
        self,
        *,
        keyword: str,
        max_items: int,
        max_pages: int,
        job_type: str = "all",
        fetch_detail: bool = False,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="scan",
            status="started",
            trace_id=trace_id,
            payload={
                "keyword": keyword,
                "max_items": max_items,
                "max_pages": max_pages,
                "job_type": job_type,
                "fetch_detail": fetch_detail,
            },
        )
        try:
            scan_result = self._connector.scan_jobs(
                keyword=keyword,
                max_items=max_items,
                max_pages=max_pages,
                job_type=job_type,
            )
            items_raw = list(scan_result.get("items") or [])
            errors: list[str] = [str(item)[:400] for item in list(scan_result.get("errors") or [])]
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in items_raw:
                if not isinstance(row, dict):
                    continue
                item = self._normalize_scan_item(keyword, row)
                dedupe_key = f"{item['job_id']}::{item['source_url']}".lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                if fetch_detail:
                    detail_result = self._connector.fetch_job_detail(
                        job_id=str(item.get("job_id") or ""),
                        source_url=str(item.get("source_url") or ""),
                    )
                    detail = detail_result.get("detail")
                    if isinstance(detail, dict) and detail:
                        item["detail"] = detail
                    error = str(detail_result.get("error") or "").strip()
                    if error:
                        errors.append(error[:400])
                normalized.append(item)
                if len(normalized) >= max(1, min(int(max_items), 80)):
                    break
            result = {
                "trace_id": trace_id,
                "keyword": keyword,
                "total": len(normalized),
                "pages_scanned": int(scan_result.get("pages_scanned") or 1),
                "screenshot_path": None,
                "items": normalized,
                "source": str(scan_result.get("source") or self._connector.provider_name),
                "provider": self._connector.provider_name,
                "execution_ready": self._connector.execution_ready,
                "errors": errors,
            }
        except Exception as exc:
            self.emit_stage_event(
                stage="scan",
                status="failed",
                trace_id=trace_id,
                payload={
                    "keyword": keyword,
                    "error": str(exc)[:500],
                },
            )
            raise
        self.emit_stage_event(
            stage="scan",
            status="completed",
            trace_id=trace_id,
            payload={
                "keyword": keyword,
                "total": int(result["total"]),
                "pages_scanned": int(result["pages_scanned"]),
                "source": result["source"],
                "errors_total": len(result["errors"]),
            },
        )
        return result

    def run_trigger(
        self,
        *,
        keyword: str,
        batch_size: int | None = None,
        match_threshold: float | None = None,
        greeting_text: str | None = None,
        job_type: str = "all",
        run_id: str | None = None,
        confirm_execute: bool = False,
        fetch_detail: bool = True,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = self.emit_stage_event(
            stage="trigger",
            status="started",
            trace_id=trace_id,
            payload={
                "keyword": keyword,
                "batch_size": batch_size,
                "match_threshold": match_threshold,
                "confirm_execute": confirm_execute,
                "fetch_detail": fetch_detail,
            },
        )
        try:
            scan = self.run_scan(
                keyword=keyword,
                max_items=30,
                max_pages=3,
                job_type=job_type,
                fetch_detail=fetch_detail,
                trace_id=trace_id,
            )
            items = list(scan.get("items") or [])
            pages_scanned = int(scan.get("pages_scanned") or 0)
            threshold = _safe_threshold(match_threshold)
            safe_batch_size = _safe_batch_size(batch_size)
            matched = [item for item in items if float(item.get("match_score") or 0.0) >= threshold]
            daily_limit = max(1, int(os.getenv("BOSS_DAILY_LIMIT", "50")))
            greeted_today = self._read_today_greeted_urls()
            deduped = [item for item in matched if str(item.get("source_url") or "") not in greeted_today]
            remaining_quota = max(0, daily_limit - len(greeted_today))
            selected = deduped[: min(safe_batch_size, remaining_quota)]
            safe_greeting_text = (greeting_text or "你好，我对这个岗位很感兴趣，期待进一步沟通。").strip()
            safe_run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%d%H%M%S")
            preview_details = [
                {
                    "run_id": safe_run_id,
                    "job_id": item.get("job_id"),
                    "job_title": item["title"],
                    "company": item["company"],
                    "match_score": item["match_score"],
                    "status": "pending_confirmation",
                    "greeting_text": safe_greeting_text,
                    "source_url": item["source_url"],
                    "source": item.get("source") or scan.get("source"),
                }
                for item in selected
            ]
            if self._hitl_required and not confirm_execute:
                result = {
                    "ok": True,
                    "trace_id": trace_id,
                    "needs_confirmation": True,
                    "execution_ready": self._connector.execution_ready,
                    "greeted": 0,
                    "failed": 0,
                    "skipped": max(0, len(items) - len(selected)),
                    "daily_count": len(greeted_today),
                    "daily_limit": daily_limit,
                    "reason": "confirmation required before real execution",
                    "pages_scanned": pages_scanned,
                    "matched_details": preview_details,
                    "source": scan.get("source"),
                    "provider": scan.get("provider"),
                    "errors": list(scan.get("errors") or []),
                }
                self.emit_stage_event(
                    stage="trigger",
                    status="preview",
                    trace_id=trace_id,
                    payload={
                        "keyword": keyword,
                        "selected_total": len(selected),
                        "pages_scanned": pages_scanned,
                        "source": scan.get("source"),
                    },
                )
                return result

            details: list[dict[str, Any]] = []
            errors = list(scan.get("errors") or [])
            for item in selected:
                action = self._connector.greet_job(
                    job=item,
                    greeting_text=safe_greeting_text,
                    run_id=safe_run_id,
                )
                ok = bool(action.get("ok"))
                error = str(action.get("error") or "").strip()
                if error:
                    errors.append(error[:400])
                details.append(
                    {
                        "run_id": safe_run_id,
                        "job_id": item.get("job_id"),
                        "job_title": item["title"],
                        "company": item["company"],
                        "match_score": item["match_score"],
                        "status": "greeted" if ok else str(action.get("status") or "failed"),
                        "greeting_text": safe_greeting_text,
                        "source_url": item["source_url"],
                        "source": action.get("source") or item.get("source") or scan.get("source"),
                        "provider": action.get("provider") or scan.get("provider"),
                        "error": error or None,
                        "attempts": int(action.get("attempts") or 0),
                    }
                )
            self._append_greet_logs(details)
            greeted_count = sum(1 for row in details if row.get("status") == "greeted")
            failed_count = len(details) - greeted_count
            self._notifier.send(
                Notification(
                    level="info",
                    title="boss_greet trigger",
                    content=f"keyword={keyword}; greeted={greeted_count}; failed={failed_count}; threshold={threshold}",
                    metadata={"run_id": safe_run_id},
                )
            )
            result = {
                "ok": True,
                "trace_id": trace_id,
                "needs_confirmation": False,
                "execution_ready": self._connector.execution_ready,
                "greeted": greeted_count,
                "failed": failed_count,
                "skipped": max(0, len(items) - len(selected)),
                "daily_count": len(greeted_today) + greeted_count,
                "daily_limit": daily_limit,
                "reason": None if details else "no job passed threshold",
                "pages_scanned": pages_scanned,
                "matched_details": details,
                "source": scan.get("source"),
                "provider": scan.get("provider"),
                "errors": errors,
            }
        except Exception as exc:
            self.emit_stage_event(
                stage="trigger",
                status="failed",
                trace_id=trace_id,
                payload={
                    "keyword": keyword,
                    "error": str(exc)[:500],
                },
            )
            raise
        self.emit_stage_event(
            stage="trigger",
            status="completed",
            trace_id=trace_id,
            payload={
                "keyword": keyword,
                "greeted": int(result["greeted"]),
                "failed": int(result["failed"]),
                "skipped": int(result["skipped"]),
                "source": result["source"],
            },
        )
        return result

    def _read_today_greeted_urls(self) -> set[str]:
        path = self._greet_log_path
        if not path.is_file():
            return set()
        today = datetime.now(timezone.utc).date().isoformat()
        rows: set[str] = set()
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
            ts = str(item.get("greeted_at") or "")
            if not ts.startswith(today):
                continue
            if str(item.get("status") or "").strip() != "greeted":
                continue
            source_url = str(item.get("source_url") or "").strip()
            if source_url:
                rows.add(source_url)
        return rows

    def _append_greet_logs(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            from ...core.storage.engine import DatabaseEngine
            db = DatabaseEngine()
            for row in rows:
                payload = {
                    "run_id": str(row.get("run_id") or ""),
                    "job_id": str(row.get("job_id") or ""),
                    "job_title": str(row.get("job_title") or ""),
                    "company": str(row.get("company") or ""),
                    "match_score": float(row.get("match_score") or 0.0),
                    "source_url": str(row.get("source_url") or ""),
                    "source": str(row.get("source") or ""),
                    "provider": str(row.get("provider") or ""),
                    "status": str(row.get("status") or "unknown"),
                    "error": str(row.get("error") or "") or None,
                    "attempts": int(row.get("attempts") or 0),
                    "greeted_at": now_iso,
                }
                safe_job_id: str | None = None
                raw_job_id = str(payload.get("job_id") or "").strip()
                if raw_job_id:
                    exists = db.execute(
                        "SELECT id FROM jobs WHERE id = %s LIMIT 1",
                        (raw_job_id,),
                        fetch="one",
                    )
                    if exists is not None:
                        safe_job_id = raw_job_id
                import uuid
                db.execute(
                    """INSERT INTO actions(id, job_id, action_type, input_summary, output_summary, status, created_at)
                       VALUES (%s, %s, 'greet', %s, %s, %s, NOW())""",
                    (
                        uuid.uuid4().hex,
                        safe_job_id,
                        json.dumps({"job_title": payload["job_title"], "company": payload["company"]}, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False),
                        payload["status"],
                    ),
                )
        except Exception:
            self._greet_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._greet_log_path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    payload = {
                        "run_id": str(row.get("run_id") or ""),
                        "job_id": str(row.get("job_id") or ""),
                        "status": str(row.get("status") or "unknown"),
                        "greeted_at": now_iso,
                    }
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def handle_intent(
        self,
        intent: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        _ = metadata
        if intent == "boss.scan":
            keyword = self._extract_keyword(text, fallback="AI Agent 实习")
            trace_id = str((metadata or {}).get("trace_id") or "").strip() or None
            return self.run_scan(keyword=keyword, max_items=10, max_pages=1, trace_id=trace_id)
        if intent == "boss.greet.trigger":
            keyword = self._extract_keyword(text, fallback="AI Agent 实习")
            trace_id = str((metadata or {}).get("trace_id") or "").strip() or None
            return self.run_trigger(keyword=keyword, batch_size=3, match_threshold=None, trace_id=trace_id)
        return None

    def register_routes(self, router: APIRouter) -> None:
        @router.get("/health")
        async def health() -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            connector_health = self._connector.health()
            return {
                "module": self.name,
                "status": "ok",
                "runtime": {
                    "mode": "real_connector" if self._connector.execution_ready else "degraded_connector",
                    "provider": self._connector.provider_name,
                    "hitl_required": self._hitl_required,
                    "greet_log_path": str(self._greet_log_path),
                    "connector": connector_health,
                    "weekday_guard": {
                        "is_weekend": is_weekend(now),
                        "is_active_hour": is_active_hour(
                            now,
                            weekday_start=9,
                            weekday_end=23,
                            weekend_start=10,
                            weekend_end=22,
                        ),
                    },
                },
            }

        @router.post("/scan")
        async def scan(payload: BossScanRunRequest) -> dict[str, Any]:
            return self.run_scan(
                keyword=payload.keyword,
                max_items=payload.max_items,
                max_pages=payload.max_pages,
                job_type=payload.job_type,
                fetch_detail=payload.fetch_detail,
            )

        @router.post("/trigger")
        async def trigger(payload: BossGreetTriggerRequest) -> dict[str, Any]:
            return self.run_trigger(
                keyword=payload.keyword,
                batch_size=payload.batch_size,
                match_threshold=payload.match_threshold,
                greeting_text=payload.greeting_text,
                job_type=payload.job_type,
                run_id=payload.run_id,
                confirm_execute=payload.confirm_execute,
                fetch_detail=payload.fetch_detail,
            )

        @router.get("/session/check")
        async def session_check() -> dict[str, Any]:
            return self._connector.check_login()

def get_module() -> BossGreetModule:
    return BossGreetModule()
