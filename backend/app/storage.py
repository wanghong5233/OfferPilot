from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

import psycopg

from .schemas import (
    ActionTimelineItem,
    AgentEvalMetricsResponse,
    EmailEventItem,
    JDAnalyzeResponse,
    JobListItem,
    MaterialDraft,
    PendingMaterialItem,
    ScheduleEventItem,
)

logger = logging.getLogger(__name__)


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:offerpilot@localhost:15433/offerpilot",
    )


def persist_jd_analysis(
    jd_text: str,
    analysis: JDAnalyzeResponse,
    source: str = "manual",
    source_url: str | None = None,
) -> str | None:
    """
    Persist JD analysis into PostgreSQL.

    - jobs: business record
    - actions: audit record for parsing/matching
    """
    job_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    now = datetime.utcnow()

    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (
                        id, title, company, source, source_url, jd_raw,
                        jd_parsed, match_score, gap_analysis, status,
                        created_at, updated_at
                    ) VALUES (
                        %(id)s, %(title)s, %(company)s, %(source)s, %(source_url)s, %(jd_raw)s,
                        %(jd_parsed)s::jsonb, %(match_score)s, %(gap_analysis)s, %(status)s,
                        %(created_at)s, %(updated_at)s
                    )
                    """,
                    {
                        "id": job_id,
                        "title": analysis.title,
                        "company": analysis.company,
                        "source": source,
                        "source_url": source_url,
                        "jd_raw": jd_text,
                        "jd_parsed": json.dumps(
                            {
                                "title": analysis.title,
                                "company": analysis.company,
                                "skills": analysis.skills,
                            },
                            ensure_ascii=False,
                        ),
                        "match_score": analysis.match_score,
                        "gap_analysis": analysis.gap_analysis,
                        "status": "new",
                        "created_at": now,
                        "updated_at": now,
                    },
                )

                cur.execute(
                    """
                    INSERT INTO actions (
                        id, job_id, action_type, input_summary,
                        output_summary, status, created_at
                    ) VALUES (
                        %(id)s, %(job_id)s, %(action_type)s, %(input_summary)s,
                        %(output_summary)s, %(status)s, %(created_at)s
                    )
                    """,
                    {
                        "id": action_id,
                        "job_id": job_id,
                        "action_type": "jd_parse",
                        "input_summary": jd_text[:3000],
                        "output_summary": (
                            f"title={analysis.title}; company={analysis.company}; "
                            f"score={analysis.match_score}; skills={','.join(analysis.skills)}"
                        )[:3000],
                        "status": "success",
                        "created_at": now,
                    },
                )
            conn.commit()
        return job_id
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Persist JD analysis failed: %s", exc)
        return None


def get_recent_jobs(limit: int = 20) -> list[JobListItem]:
    safe_limit = max(1, min(limit, 200))
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, company, source, match_score, status, created_at
                    FROM jobs
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": safe_limit},
                )
                rows = cur.fetchall()
        return [
            JobListItem(
                id=row[0],
                title=row[1],
                company=row[2],
                source=row[3],
                match_score=float(row[4]) if row[4] is not None else None,
                status=row[5],
                created_at=row[6],
            )
            for row in rows
        ]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Query recent jobs failed: %s", exc)
        return []


def get_job_detail(job_id: str) -> dict[str, Any] | None:
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, title, company, jd_raw, jd_parsed, match_score, source, status
                    FROM jobs
                    WHERE id = %(job_id)s
                    LIMIT 1
                    """,
                    {"job_id": job_id},
                )
                row = cur.fetchone()
        if not row:
            return None

        jd_parsed = row[4]
        if isinstance(jd_parsed, str):
            try:
                jd_parsed = json.loads(jd_parsed)
            except json.JSONDecodeError:
                jd_parsed = None

        return {
            "id": row[0],
            "title": row[1],
            "company": row[2],
            "jd_raw": row[3],
            "jd_parsed": jd_parsed if isinstance(jd_parsed, dict) else None,
            "match_score": float(row[5]) if row[5] is not None else None,
            "source": row[6],
            "status": row[7],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Query job detail failed: %s", exc)
        return None


def log_action(
    *,
    job_id: str | None,
    action_type: str,
    input_summary: str | None = None,
    output_summary: str | None = None,
    screenshot_path: str | None = None,
    status: str = "success",
) -> str | None:
    from .agent_events import EventType, emit

    action_id = str(uuid.uuid4())
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO actions (
                        id, job_id, action_type, input_summary,
                        output_summary, screenshot_path, status, created_at
                    ) VALUES (
                        %(id)s, %(job_id)s, %(action_type)s, %(input_summary)s,
                        %(output_summary)s, %(screenshot_path)s, %(status)s, %(created_at)s
                    )
                    """,
                    {
                        "id": action_id,
                        "job_id": job_id,
                        "action_type": action_type[:80],
                        "input_summary": (input_summary or "")[:3000],
                        "output_summary": (output_summary or "")[:3000],
                        "screenshot_path": (screenshot_path or "")[:500] or None,
                        "status": status[:80],
                        "created_at": now,
                    },
                )
            conn.commit()
        emit(
            EventType.ACTION_LOGGED,
            f"[{action_type}] {status}",
            action_id=action_id,
            action_type=action_type,
            status=status,
            screenshot_path=screenshot_path,
        )
        return action_id
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Insert action failed: %s", exc)
        return None


def create_application_record(
    *,
    job_id: str,
    resume_version: str,
    cover_letter: str,
    channel: str = "manual_review",
    notes: str | None = None,
) -> str | None:
    app_id = str(uuid.uuid4())
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO applications (
                        id, job_id, resume_version, cover_letter,
                        applied_at, channel, notes, created_at
                    ) VALUES (
                        %(id)s, %(job_id)s, %(resume_version)s, %(cover_letter)s,
                        %(applied_at)s, %(channel)s, %(notes)s, %(created_at)s
                    )
                    """,
                    {
                        "id": app_id,
                        "job_id": job_id,
                        "resume_version": resume_version[:120],
                        "cover_letter": cover_letter[:8000],
                        "applied_at": now,
                        "channel": channel[:120],
                        "notes": (notes or "")[:3000],
                        "created_at": now,
                    },
                )
            conn.commit()
        return app_id
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Insert application failed: %s", exc)
        return None


def _ensure_resume_sources_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_sources (
                source_id TEXT PRIMARY KEY,
                resume_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.commit()


def upsert_resume_source(source_id: str, resume_text: str) -> bool:
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_resume_sources_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO resume_sources (source_id, resume_text, created_at, updated_at)
                    VALUES (%(source_id)s, %(resume_text)s, %(created_at)s, %(updated_at)s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        resume_text = EXCLUDED.resume_text,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "source_id": source_id[:120],
                        "resume_text": resume_text,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Upsert resume source failed: %s", exc)
        return False


def get_resume_source(source_id: str) -> dict[str, Any] | None:
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_resume_sources_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_id, resume_text, updated_at
                    FROM resume_sources
                    WHERE source_id = %(source_id)s
                    LIMIT 1
                    """,
                    {"source_id": source_id[:120]},
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "source_id": row[0],
            "resume_text": row[1],
            "updated_at": row[2],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Get resume source failed: %s", exc)
        return None


def _ensure_user_profiles_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                profile_id TEXT PRIMARY KEY,
                profile_json JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.commit()


def upsert_user_profile(profile_id: str, profile: dict[str, Any]) -> bool:
    now = datetime.utcnow()
    safe_profile_id = profile_id.strip()[:120] or "default"
    safe_profile = profile if isinstance(profile, dict) else {}
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_user_profiles_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_profiles (profile_id, profile_json, created_at, updated_at)
                    VALUES (%(profile_id)s, %(profile_json)s::jsonb, %(created_at)s, %(updated_at)s)
                    ON CONFLICT (profile_id) DO UPDATE SET
                        profile_json = EXCLUDED.profile_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "profile_id": safe_profile_id,
                        "profile_json": json.dumps(safe_profile, ensure_ascii=False),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Upsert user profile failed: %s", exc)
        return False


def get_user_profile(profile_id: str) -> dict[str, Any] | None:
    safe_profile_id = profile_id.strip()[:120] or "default"
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_user_profiles_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT profile_id, profile_json, updated_at
                    FROM user_profiles
                    WHERE profile_id = %(profile_id)s
                    LIMIT 1
                    """,
                    {"profile_id": safe_profile_id},
                )
                row = cur.fetchone()
        if not row:
            return None
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        return {
            "profile_id": str(row[0]),
            "profile": payload if isinstance(payload, dict) else {},
            "updated_at": row[2],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Get user profile failed: %s", exc)
        return None


def _ensure_boss_chat_events_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_chat_events (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                hr_name TEXT,
                company TEXT,
                job_title TEXT,
                latest_hr_message TEXT NOT NULL,
                latest_hr_time TEXT,
                message_signature TEXT NOT NULL UNIQUE,
                intent TEXT,
                confidence REAL,
                action TEXT,
                reason TEXT,
                reply_text TEXT,
                needs_send_resume BOOLEAN DEFAULT FALSE,
                needs_user_intervention BOOLEAN DEFAULT FALSE,
                notification_sent BOOLEAN DEFAULT FALSE,
                notification_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_boss_chat_events_conversation_created
            ON boss_chat_events (conversation_id, created_at DESC)
            """
        )
    conn.commit()


def get_boss_chat_event_by_signature(message_signature: str) -> dict[str, Any] | None:
    safe_signature = message_signature.strip()[:120]
    if not safe_signature:
        return None
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_boss_chat_events_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, conversation_id, hr_name, company, job_title,
                        latest_hr_message, latest_hr_time, message_signature,
                        intent, confidence, action, reason, reply_text,
                        needs_send_resume, needs_user_intervention,
                        notification_sent, notification_error,
                        created_at, updated_at
                    FROM boss_chat_events
                    WHERE message_signature = %(message_signature)s
                    LIMIT 1
                    """,
                    {"message_signature": safe_signature},
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "conversation_id": str(row[1]),
            "hr_name": str(row[2] or ""),
            "company": str(row[3] or "") or None,
            "job_title": str(row[4] or "") or None,
            "latest_hr_message": str(row[5] or ""),
            "latest_hr_time": str(row[6] or "") or None,
            "message_signature": str(row[7]),
            "intent": str(row[8] or "") or None,
            "confidence": float(row[9]) if row[9] is not None else None,
            "action": str(row[10] or "") or None,
            "reason": str(row[11] or "") or None,
            "reply_text": str(row[12] or "") or None,
            "needs_send_resume": bool(row[13]),
            "needs_user_intervention": bool(row[14]),
            "notification_sent": bool(row[15]),
            "notification_error": str(row[16] or "") or None,
            "created_at": row[17],
            "updated_at": row[18],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Get boss chat event by signature failed: %s", exc)
        return None


def insert_boss_chat_event(
    *,
    conversation_id: str,
    hr_name: str,
    company: str | None,
    job_title: str | None,
    latest_hr_message: str,
    latest_hr_time: str | None,
    message_signature: str,
    intent: str,
    confidence: float,
    action: str,
    reason: str,
    reply_text: str | None,
    needs_send_resume: bool,
    needs_user_intervention: bool,
    notification_sent: bool,
    notification_error: str | None = None,
) -> tuple[bool, str | None]:
    now = datetime.utcnow()
    event_id = str(uuid.uuid4())
    safe_signature = message_signature.strip()[:120]
    if not safe_signature:
        return False, None
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_boss_chat_events_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO boss_chat_events (
                        id, conversation_id, hr_name, company, job_title,
                        latest_hr_message, latest_hr_time, message_signature,
                        intent, confidence, action, reason, reply_text,
                        needs_send_resume, needs_user_intervention,
                        notification_sent, notification_error,
                        created_at, updated_at
                    ) VALUES (
                        %(id)s, %(conversation_id)s, %(hr_name)s, %(company)s, %(job_title)s,
                        %(latest_hr_message)s, %(latest_hr_time)s, %(message_signature)s,
                        %(intent)s, %(confidence)s, %(action)s, %(reason)s, %(reply_text)s,
                        %(needs_send_resume)s, %(needs_user_intervention)s,
                        %(notification_sent)s, %(notification_error)s,
                        %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (message_signature) DO NOTHING
                    """,
                    {
                        "id": event_id,
                        "conversation_id": conversation_id[:200],
                        "hr_name": hr_name[:300],
                        "company": (company or "")[:300] or None,
                        "job_title": (job_title or "")[:300] or None,
                        "latest_hr_message": latest_hr_message[:4000],
                        "latest_hr_time": (latest_hr_time or "")[:120] or None,
                        "message_signature": safe_signature,
                        "intent": intent[:80],
                        "confidence": max(0.0, min(float(confidence), 1.0)),
                        "action": action[:80],
                        "reason": reason[:1000],
                        "reply_text": (reply_text or "")[:4000] or None,
                        "needs_send_resume": bool(needs_send_resume),
                        "needs_user_intervention": bool(needs_user_intervention),
                        "notification_sent": bool(notification_sent),
                        "notification_error": (notification_error or "")[:1000] or None,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                inserted = cur.rowcount > 0
            conn.commit()
        return inserted, (event_id if inserted else None)
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Insert boss chat event failed: %s", exc)
        return False, None


def _ensure_material_threads_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS material_threads (
                thread_id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id),
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                match_score REAL,
                resume_version TEXT NOT NULL,
                status TEXT NOT NULL,
                draft JSONB,
                last_feedback TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_material_threads_status_updated
            ON material_threads (status, updated_at DESC)
            """
        )
    conn.commit()


def upsert_material_thread(
    *,
    thread_id: str,
    job_id: str,
    title: str,
    company: str,
    match_score: float | None,
    resume_version: str,
    status: str,
    draft: MaterialDraft | dict[str, Any] | None,
    feedback: str | None = None,
) -> bool:
    now = datetime.utcnow()
    if isinstance(draft, MaterialDraft):
        draft_json = draft.model_dump()
    elif isinstance(draft, dict):
        draft_json = draft
    else:
        draft_json = None

    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_material_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO material_threads (
                        thread_id, job_id, title, company, match_score,
                        resume_version, status, draft, last_feedback,
                        created_at, updated_at
                    ) VALUES (
                        %(thread_id)s, %(job_id)s, %(title)s, %(company)s, %(match_score)s,
                        %(resume_version)s, %(status)s, %(draft)s::jsonb, %(last_feedback)s,
                        %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (thread_id) DO UPDATE SET
                        job_id = EXCLUDED.job_id,
                        title = EXCLUDED.title,
                        company = EXCLUDED.company,
                        match_score = EXCLUDED.match_score,
                        resume_version = EXCLUDED.resume_version,
                        status = EXCLUDED.status,
                        draft = EXCLUDED.draft,
                        last_feedback = EXCLUDED.last_feedback,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "thread_id": thread_id,
                        "job_id": job_id,
                        "title": title[:300],
                        "company": company[:300],
                        "match_score": match_score,
                        "resume_version": resume_version[:120],
                        "status": status[:80],
                        "draft": json.dumps(draft_json, ensure_ascii=False)
                        if draft_json is not None
                        else None,
                        "last_feedback": (feedback or "")[:3000],
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Upsert material thread failed: %s", exc)
        return False


def get_material_thread(thread_id: str) -> dict[str, Any] | None:
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_material_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        thread_id, job_id, title, company, match_score,
                        resume_version, status, draft, last_feedback,
                        created_at, updated_at
                    FROM material_threads
                    WHERE thread_id = %(thread_id)s
                    LIMIT 1
                    """,
                    {"thread_id": thread_id},
                )
                row = cur.fetchone()
        if not row:
            return None
        draft = row[7]
        if isinstance(draft, str):
            try:
                draft = json.loads(draft)
            except json.JSONDecodeError:
                draft = None
        return {
            "thread_id": row[0],
            "job_id": row[1],
            "title": row[2],
            "company": row[3],
            "match_score": float(row[4]) if row[4] is not None else None,
            "resume_version": row[5],
            "status": row[6],
            "draft": draft if isinstance(draft, dict) else None,
            "last_feedback": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Get material thread failed: %s", exc)
        return None


def list_pending_material_threads(limit: int = 50) -> list[PendingMaterialItem]:
    safe_limit = max(1, min(limit, 200))
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_material_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        thread_id, job_id, title, company, match_score,
                        resume_version, draft, created_at, updated_at
                    FROM material_threads
                    WHERE status = 'pending_review'
                    ORDER BY updated_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": safe_limit},
                )
                rows = cur.fetchall()
        result: list[PendingMaterialItem] = []
        for row in rows:
            draft = row[6]
            if isinstance(draft, str):
                try:
                    draft = json.loads(draft)
                except json.JSONDecodeError:
                    draft = None
            draft_model = MaterialDraft.model_validate(draft) if isinstance(draft, dict) else None
            result.append(
                PendingMaterialItem(
                    thread_id=row[0],
                    job_id=row[1],
                    title=row[2],
                    company=row[3],
                    match_score=float(row[4]) if row[4] is not None else None,
                    resume_version=row[5],
                    created_at=row[7],
                    updated_at=row[8],
                    draft=draft_model,
                )
            )
        return result
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List pending material threads failed: %s", exc)
        return []


def _ensure_form_fill_threads_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS form_fill_threads (
                thread_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                profile JSONB,
                preview JSONB,
                fill_result JSONB,
                last_feedback TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_form_fill_threads_status_updated
            ON form_fill_threads (status, updated_at DESC)
            """
        )
    conn.commit()


def upsert_form_fill_thread(
    *,
    thread_id: str,
    url: str,
    status: str,
    profile: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
    fill_result: dict[str, Any] | None = None,
    feedback: str | None = None,
) -> bool:
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_form_fill_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO form_fill_threads (
                        thread_id, url, status, profile, preview,
                        fill_result, last_feedback, created_at, updated_at
                    ) VALUES (
                        %(thread_id)s, %(url)s, %(status)s, %(profile)s::jsonb, %(preview)s::jsonb,
                        %(fill_result)s::jsonb, %(last_feedback)s, %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (thread_id) DO UPDATE SET
                        url = EXCLUDED.url,
                        status = EXCLUDED.status,
                        profile = EXCLUDED.profile,
                        preview = EXCLUDED.preview,
                        fill_result = EXCLUDED.fill_result,
                        last_feedback = EXCLUDED.last_feedback,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "thread_id": thread_id,
                        "url": url[:1200],
                        "status": status[:80],
                        "profile": json.dumps(profile or {}, ensure_ascii=False),
                        "preview": json.dumps(preview, ensure_ascii=False) if preview is not None else None,
                        "fill_result": (
                            json.dumps(fill_result, ensure_ascii=False) if fill_result is not None else None
                        ),
                        "last_feedback": (feedback or "")[:3000],
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Upsert form fill thread failed: %s", exc)
        return False


def get_form_fill_thread(thread_id: str) -> dict[str, Any] | None:
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_form_fill_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        thread_id, url, status, profile, preview,
                        fill_result, last_feedback, created_at, updated_at
                    FROM form_fill_threads
                    WHERE thread_id = %(thread_id)s
                    LIMIT 1
                    """,
                    {"thread_id": thread_id},
                )
                row = cur.fetchone()
        if not row:
            return None

        def _json_or_none(value: Any) -> dict[str, Any] | None:
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
            return None

        profile = _json_or_none(row[3]) or {}
        preview = _json_or_none(row[4])
        fill_result = _json_or_none(row[5])
        return {
            "thread_id": row[0],
            "url": row[1],
            "status": row[2],
            "profile": profile,
            "preview": preview,
            "fill_result": fill_result,
            "last_feedback": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Get form fill thread failed: %s", exc)
        return None


def list_pending_form_fill_threads(limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_form_fill_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT thread_id, url, status, preview, created_at, updated_at
                    FROM form_fill_threads
                    WHERE status = 'pending_review'
                    ORDER BY updated_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": safe_limit},
                )
                rows = cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            preview: dict[str, Any] | None = None
            raw_preview = row[3]
            if isinstance(raw_preview, dict):
                preview = raw_preview
            elif isinstance(raw_preview, str):
                try:
                    parsed = json.loads(raw_preview)
                    if isinstance(parsed, dict):
                        preview = parsed
                except json.JSONDecodeError:
                    preview = None
            mapped_fields = 0
            if isinstance(preview, dict):
                mapped_fields = int(preview.get("mapped_fields") or 0)
            result.append(
                {
                    "thread_id": row[0],
                    "url": row[1],
                    "status": row[2],
                    "mapped_fields": mapped_fields,
                    "created_at": row[4],
                    "updated_at": row[5],
                }
            )
        return result
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List pending form fill threads failed: %s", exc)
        return []


def _ensure_email_events_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS email_events (
                id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                email_type TEXT NOT NULL,
                company TEXT,
                interview_time TEXT,
                raw_classification JSONB,
                related_job_id TEXT,
                updated_job_status TEXT,
                received_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_email_events_created
            ON email_events (created_at DESC)
            """
        )
    conn.commit()


def _ensure_schedules_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                source_email_id TEXT,
                company TEXT,
                event_type TEXT NOT NULL,
                start_at TIMESTAMP NOT NULL,
                raw_time_text TEXT,
                mode TEXT,
                location TEXT,
                contact TEXT,
                confidence REAL,
                status TEXT DEFAULT 'scheduled',
                reminder_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_schedules_upcoming
            ON schedules (status, start_at ASC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_schedules_reminder
            ON schedules (reminder_sent_at, start_at ASC)
            """
        )
    conn.commit()


def _schedule_signature(
    *,
    source_email_id: str | None,
    company: str | None,
    event_type: str,
    start_at: datetime,
) -> str:
    stamp = start_at.strftime("%Y-%m-%d %H:%M")
    raw = f"{(source_email_id or '').strip()}|{(company or '').strip().lower()}|{event_type.strip()}|{stamp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _safe_schedule_event_type(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"interview", "written_test", "other"}:
        return value
    return "other"


def _safe_schedule_mode(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"online", "offline", "unknown"}:
        return value
    return "unknown"


def _safe_schedule_status(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"scheduled", "completed", "cancelled"}:
        return value
    return "scheduled"


def upsert_schedule_event(
    *,
    source_email_id: str | None,
    company: str | None,
    event_type: str,
    start_at: datetime,
    raw_time_text: str | None = None,
    mode: str | None = None,
    location: str | None = None,
    contact: str | None = None,
    confidence: float | None = None,
    status: str = "scheduled",
) -> str | None:
    safe_event_type = _safe_schedule_event_type(event_type)
    safe_mode = _safe_schedule_mode(mode)
    safe_status = _safe_schedule_status(status)
    now = datetime.utcnow()
    signature = _schedule_signature(
        source_email_id=source_email_id,
        company=company,
        event_type=safe_event_type,
        start_at=start_at,
    )
    schedule_id = str(uuid.uuid4())
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_schedules_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO schedules (
                        id, signature, source_email_id, company, event_type, start_at,
                        raw_time_text, mode, location, contact, confidence, status,
                        reminder_sent_at, created_at, updated_at
                    ) VALUES (
                        %(id)s, %(signature)s, %(source_email_id)s, %(company)s, %(event_type)s, %(start_at)s,
                        %(raw_time_text)s, %(mode)s, %(location)s, %(contact)s, %(confidence)s, %(status)s,
                        NULL, %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (signature) DO UPDATE SET
                        source_email_id = EXCLUDED.source_email_id,
                        company = EXCLUDED.company,
                        raw_time_text = EXCLUDED.raw_time_text,
                        mode = EXCLUDED.mode,
                        location = EXCLUDED.location,
                        contact = EXCLUDED.contact,
                        confidence = EXCLUDED.confidence,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    {
                        "id": schedule_id,
                        "signature": signature,
                        "source_email_id": source_email_id,
                        "company": (company or "")[:300] or None,
                        "event_type": safe_event_type[:40],
                        "start_at": start_at,
                        "raw_time_text": (raw_time_text or "")[:200] or None,
                        "mode": safe_mode[:20],
                        "location": (location or "")[:300] or None,
                        "contact": (contact or "")[:200] or None,
                        "confidence": float(confidence) if confidence is not None else None,
                        "status": safe_status[:40],
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                row = cur.fetchone()
            conn.commit()
        if row and row[0]:
            return str(row[0])
        return None
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Upsert schedule event failed: %s", exc)
        return None


def list_upcoming_schedules(*, limit: int = 50, days: int = 14) -> list[ScheduleEventItem]:
    safe_limit = max(1, min(limit, 200))
    safe_days = max(1, min(days, 90))
    now = datetime.utcnow()
    end_at = now + timedelta(days=safe_days)
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_schedules_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, company, event_type, start_at, raw_time_text, mode, location,
                        contact, confidence, status, source_email_id, reminder_sent_at, created_at
                    FROM schedules
                    WHERE status = 'scheduled'
                      AND start_at >= %(now)s
                      AND start_at <= %(end_at)s
                    ORDER BY start_at ASC
                    LIMIT %(limit)s
                    """,
                    {"now": now, "end_at": end_at, "limit": safe_limit},
                )
                rows = cur.fetchall()
        return [
            ScheduleEventItem(
                id=row[0],
                company=row[1],
                event_type=_safe_schedule_event_type(row[2]),
                start_at=row[3],
                raw_time_text=row[4],
                mode=_safe_schedule_mode(row[5]),
                location=row[6],
                contact=row[7],
                confidence=float(row[8] or 0.0),
                status=_safe_schedule_status(row[9]),
                source_email_id=row[10],
                reminder_sent_at=row[11],
                created_at=row[12],
            )
            for row in rows
        ]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List upcoming schedules failed: %s", exc)
        return []


def list_due_schedule_reminders(*, within_hours: int = 24, limit: int = 20) -> list[ScheduleEventItem]:
    safe_hours = max(1, min(within_hours, 168))
    safe_limit = max(1, min(limit, 200))
    now = datetime.utcnow()
    end_at = now + timedelta(hours=safe_hours)
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_schedules_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, company, event_type, start_at, raw_time_text, mode, location,
                        contact, confidence, status, source_email_id, reminder_sent_at, created_at
                    FROM schedules
                    WHERE status = 'scheduled'
                      AND start_at >= %(now)s
                      AND start_at <= %(end_at)s
                      AND reminder_sent_at IS NULL
                    ORDER BY start_at ASC
                    LIMIT %(limit)s
                    """,
                    {"now": now, "end_at": end_at, "limit": safe_limit},
                )
                rows = cur.fetchall()
        return [
            ScheduleEventItem(
                id=row[0],
                company=row[1],
                event_type=_safe_schedule_event_type(row[2]),
                start_at=row[3],
                raw_time_text=row[4],
                mode=_safe_schedule_mode(row[5]),
                location=row[6],
                contact=row[7],
                confidence=float(row[8] or 0.0),
                status=_safe_schedule_status(row[9]),
                source_email_id=row[10],
                reminder_sent_at=row[11],
                created_at=row[12],
            )
            for row in rows
        ]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List due schedule reminders failed: %s", exc)
        return []


def mark_schedule_reminded(schedule_ids: list[str]) -> int:
    ids = [sid for sid in schedule_ids if sid and sid.strip()]
    if not ids:
        return 0
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_schedules_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE schedules
                    SET reminder_sent_at = %(now)s, updated_at = %(now)s
                    WHERE id = ANY(%(ids)s)
                      AND reminder_sent_at IS NULL
                    """,
                    {"now": now, "ids": ids},
                )
                changed = cur.rowcount
            conn.commit()
        return int(changed or 0)
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Mark schedule reminded failed: %s", exc)
        return 0


def persist_email_event(
    *,
    sender: str,
    subject: str,
    body: str,
    email_type: str,
    company: str | None = None,
    interview_time: str | None = None,
    raw_classification: dict[str, Any] | None = None,
    related_job_id: str | None = None,
    updated_job_status: str | None = None,
    received_at: datetime | None = None,
) -> str | None:
    email_id = str(uuid.uuid4())
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_email_events_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO email_events (
                        id, sender, subject, body, email_type, company, interview_time,
                        raw_classification, related_job_id, updated_job_status, received_at, created_at
                    ) VALUES (
                        %(id)s, %(sender)s, %(subject)s, %(body)s, %(email_type)s, %(company)s, %(interview_time)s,
                        %(raw_classification)s::jsonb, %(related_job_id)s, %(updated_job_status)s, %(received_at)s, %(created_at)s
                    )
                    """,
                    {
                        "id": email_id,
                        "sender": sender[:400],
                        "subject": subject[:1000],
                        "body": body[:12000],
                        "email_type": email_type[:80],
                        "company": (company or "")[:300] or None,
                        "interview_time": (interview_time or "")[:200] or None,
                        "raw_classification": (
                            json.dumps(raw_classification, ensure_ascii=False)
                            if raw_classification is not None
                            else None
                        ),
                        "related_job_id": related_job_id,
                        "updated_job_status": (updated_job_status or "")[:80] or None,
                        "received_at": received_at,
                        "created_at": now,
                    },
                )
            conn.commit()
        return email_id
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Persist email event failed: %s", exc)
        return None


def list_recent_email_events(limit: int = 50) -> list[EmailEventItem]:
    safe_limit = max(1, min(limit, 200))
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_email_events_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, sender, subject, email_type, company, interview_time,
                        related_job_id, updated_job_status, created_at
                    FROM email_events
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": safe_limit},
                )
                rows = cur.fetchall()
        return [
            EmailEventItem(
                id=row[0],
                sender=row[1],
                subject=row[2],
                email_type=row[3],
                company=row[4],
                interview_time=row[5],
                related_job_id=row[6],
                updated_job_status=row[7],
                created_at=row[8],
            )
            for row in rows
        ]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List recent email events failed: %s", exc)
        return []


def find_job_id_by_company(company: str) -> str | None:
    name = company.strip()
    if not name:
        return None
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE lower(company) LIKE %(pattern)s
                       OR %(name)s LIKE '%%' || lower(company) || '%%'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    {
                        "pattern": f"%{name.lower()}%",
                        "name": name.lower(),
                    },
                )
                row = cur.fetchone()
        if not row:
            return None
        return str(row[0])
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Find job by company failed: %s", exc)
        return None


def update_job_status(job_id: str, status: str) -> bool:
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = %(status)s, updated_at = %(updated_at)s
                    WHERE id = %(job_id)s
                    """,
                    {
                        "status": status[:80],
                        "updated_at": now,
                        "job_id": job_id,
                    },
                )
                changed = cur.rowcount > 0
            conn.commit()
        return changed
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Update job status failed: %s", exc)
        return False


def list_action_timeline(limit: int = 100, action_type: str | None = None) -> list[ActionTimelineItem]:
    safe_limit = max(1, min(limit, 500))
    normalized_type = (action_type or "").strip() or None
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                if normalized_type:
                    cur.execute(
                        """
                        SELECT
                            a.id,
                            a.job_id,
                            a.action_type,
                            a.status,
                            a.input_summary,
                            a.output_summary,
                            a.screenshot_path,
                            a.created_at,
                            j.title,
                            j.company
                        FROM actions AS a
                        LEFT JOIN jobs AS j ON j.id = a.job_id
                        WHERE a.action_type = %(action_type)s
                        ORDER BY a.created_at DESC
                        LIMIT %(limit)s
                        """,
                        {
                            "action_type": normalized_type,
                            "limit": safe_limit,
                        },
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                            a.id,
                            a.job_id,
                            a.action_type,
                            a.status,
                            a.input_summary,
                            a.output_summary,
                            a.screenshot_path,
                            a.created_at,
                            j.title,
                            j.company
                        FROM actions AS a
                        LEFT JOIN jobs AS j ON j.id = a.job_id
                        ORDER BY a.created_at DESC
                        LIMIT %(limit)s
                        """,
                        {"limit": safe_limit},
                    )
                rows = cur.fetchall()
        return [
            ActionTimelineItem(
                action_id=row[0],
                job_id=row[1],
                action_type=row[2],
                status=row[3],
                input_summary=row[4],
                output_summary=row[5],
                screenshot_path=row[6],
                created_at=row[7],
                job_title=row[8],
                job_company=row[9],
            )
            for row in rows
        ]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("List action timeline failed: %s", exc)
        return []


def get_agent_eval_metrics(window_days: int = 14) -> AgentEvalMetricsResponse:
    safe_window = max(1, min(window_days, 90))
    since = datetime.utcnow() - timedelta(days=safe_window)
    evaluated_at = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_material_threads_table(conn)
            _ensure_form_fill_threads_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH grouped AS (
                        SELECT
                            lower(trim(title)) AS norm_title,
                            lower(trim(company)) AS norm_company,
                            stddev_samp(match_score) AS std
                        FROM jobs
                        WHERE match_score IS NOT NULL
                          AND created_at >= %(since)s
                        GROUP BY lower(trim(title)), lower(trim(company))
                        HAVING count(*) >= 2
                    )
                    SELECT count(*)::int AS groups, avg(std) AS mean_std
                    FROM grouped
                    """,
                    {"since": since},
                )
                score_row = cur.fetchone() or (0, None)
                score_groups = int(score_row[0] or 0)
                score_std = float(score_row[1]) if score_row[1] is not None else None

                cur.execute(
                    """
                    SELECT
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN fill_result ? 'attempted_fields'
                                         AND (fill_result->>'attempted_fields') ~ '^[0-9]+$'
                                    THEN (fill_result->>'attempted_fields')::int
                                    ELSE 0
                                END
                            ),
                            0
                        )::int AS attempted_total,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN fill_result ? 'failed_fields'
                                         AND (fill_result->>'failed_fields') ~ '^[0-9]+$'
                                    THEN (fill_result->>'failed_fields')::int
                                    ELSE 0
                                END
                            ),
                            0
                        )::int AS failed_total
                    FROM form_fill_threads
                    WHERE fill_result IS NOT NULL
                      AND updated_at >= %(since)s
                    """,
                    {"since": since},
                )
                autofill_row = cur.fetchone() or (0, 0)
                autofill_total = int(autofill_row[0] or 0)
                autofill_failed = int(autofill_row[1] or 0)
                autofill_accuracy = (
                    max(0.0, min(1.0, (autofill_total - autofill_failed) / autofill_total))
                    if autofill_total > 0
                    else None
                )

                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0)::int AS approved_count,
                        COALESCE(
                            SUM(CASE WHEN status IN ('approved', 'rejected') THEN 1 ELSE 0 END),
                            0
                        )::int AS reviewed_count
                    FROM material_threads
                    WHERE updated_at >= %(since)s
                    """,
                    {"since": since},
                )
                material_row = cur.fetchone() or (0, 0)
                material_approved = int(material_row[0] or 0)
                material_reviewed = int(material_row[1] or 0)
                material_rate = (
                    max(0.0, min(1.0, material_approved / material_reviewed))
                    if material_reviewed > 0
                    else None
                )

                cur.execute(
                    """
                    SELECT
                        percentile_cont(0.5) WITHIN GROUP (
                            ORDER BY EXTRACT(EPOCH FROM (mt.updated_at - j.created_at))
                        ) AS p50_latency_sec,
                        count(*)::int AS sample_count
                    FROM material_threads AS mt
                    JOIN jobs AS j ON j.id = mt.job_id
                    WHERE mt.status = 'approved'
                      AND mt.updated_at >= %(since)s
                      AND j.created_at IS NOT NULL
                      AND mt.updated_at >= j.created_at
                    """,
                    {"since": since},
                )
                latency_row = cur.fetchone() or (None, 0)
                latency_p50 = float(latency_row[0]) if latency_row[0] is not None else None
                latency_samples = int(latency_row[1] or 0)

        return AgentEvalMetricsResponse(
            window_days=safe_window,
            evaluated_at=evaluated_at,
            score_consistency_std=score_std,
            score_consistency_groups=score_groups,
            autofill_accuracy=autofill_accuracy,
            autofill_total_fields=autofill_total,
            autofill_failed_fields=autofill_failed,
            material_approve_rate=material_rate,
            material_approved=material_approved,
            material_reviewed=material_reviewed,
            e2e_latency_sec_p50=latency_p50,
            e2e_latency_samples=latency_samples,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Compute agent eval metrics failed: %s", exc)
        return AgentEvalMetricsResponse(
            window_days=safe_window,
            evaluated_at=evaluated_at,
        )


def _ensure_security_tokens_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS security_tokens (
                token_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                action TEXT NOT NULL,
                purpose TEXT,
                status TEXT NOT NULL,
                issued_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                consumed_at TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_security_tokens_status_expires
            ON security_tokens (status, expires_at)
            """
        )
    conn.commit()


def issue_security_token(
    *,
    action: str,
    purpose: str | None = None,
    expire_minutes: int = 10,
) -> dict[str, Any] | None:
    safe_action = action.strip()[:80]
    if not safe_action:
        return None
    now = datetime.utcnow()
    safe_minutes = max(1, min(expire_minutes, 24 * 60))
    expires_at = now + timedelta(minutes=safe_minutes)
    token_id = str(uuid.uuid4())
    token = f"ofp_tok_{secrets.token_urlsafe(24)}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_security_tokens_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO security_tokens (
                        token_id, token_hash, action, purpose, status,
                        issued_at, expires_at
                    ) VALUES (
                        %(token_id)s, %(token_hash)s, %(action)s, %(purpose)s, %(status)s,
                        %(issued_at)s, %(expires_at)s
                    )
                    """,
                    {
                        "token_id": token_id,
                        "token_hash": token_hash,
                        "action": safe_action,
                        "purpose": (purpose or "")[:300] or None,
                        "status": "issued",
                        "issued_at": now,
                        "expires_at": expires_at,
                    },
                )
            conn.commit()
        return {
            "token_id": token_id,
            "token": token,
            "action": safe_action,
            "purpose": (purpose or "")[:300] or None,
            "expires_at": expires_at,
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Issue security token failed: %s", exc)
        return None


def consume_security_token(*, token: str, action: str) -> dict[str, Any]:
    safe_action = action.strip()[:80]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_security_tokens_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token_id, status, expires_at
                    FROM security_tokens
                    WHERE token_hash = %(token_hash)s
                      AND action = %(action)s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    {
                        "token_hash": token_hash,
                        "action": safe_action,
                    },
                )
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return {"valid": False, "consumed": False, "reason": "token not found", "token_id": None}

                token_id = str(row[0])
                status = str(row[1])
                expires_at = row[2]
                if status != "issued":
                    conn.commit()
                    return {
                        "valid": False,
                        "consumed": False,
                        "reason": f"token status is {status}",
                        "token_id": token_id,
                    }
                if expires_at is not None and now > expires_at:
                    cur.execute(
                        """
                        UPDATE security_tokens
                        SET status = 'expired'
                        WHERE token_id = %(token_id)s
                        """,
                        {"token_id": token_id},
                    )
                    conn.commit()
                    return {
                        "valid": False,
                        "consumed": False,
                        "reason": "token expired",
                        "token_id": token_id,
                    }

                cur.execute(
                    """
                    UPDATE security_tokens
                    SET status = 'consumed', consumed_at = %(consumed_at)s
                    WHERE token_id = %(token_id)s
                    """,
                    {
                        "consumed_at": now,
                        "token_id": token_id,
                    },
                )
            conn.commit()
        return {"valid": True, "consumed": True, "reason": None, "token_id": token_id}
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Consume security token failed: %s", exc)
        return {"valid": False, "consumed": False, "reason": str(exc), "token_id": None}


def _ensure_tool_budgets_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_budgets (
                session_id TEXT NOT NULL,
                tool_type TEXT NOT NULL,
                used_count INTEGER NOT NULL,
                limit_count INTEGER NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (session_id, tool_type)
            )
            """
        )
    conn.commit()


def check_tool_budget(
    *,
    session_id: str,
    tool_type: str,
    limit: int,
    consume: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    safe_session = session_id.strip()[:120]
    safe_tool = tool_type.strip()[:80]
    safe_limit = max(1, min(limit, 5000))
    safe_consume = max(0, min(consume, 500))
    if not safe_session or not safe_tool:
        return {
            "session_id": safe_session,
            "tool_type": safe_tool,
            "limit": safe_limit,
            "used": 0,
            "remaining": safe_limit,
            "allowed": False,
            "reason": "session_id and tool_type are required",
        }

    now = datetime.utcnow()
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_tool_budgets_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT used_count, limit_count
                    FROM tool_budgets
                    WHERE session_id = %(session_id)s
                      AND tool_type = %(tool_type)s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    {
                        "session_id": safe_session,
                        "tool_type": safe_tool,
                    },
                )
                row = cur.fetchone()
                if row:
                    used = int(row[0] or 0)
                    current_limit = int(row[1] or safe_limit)
                    if current_limit != safe_limit:
                        current_limit = safe_limit
                        cur.execute(
                            """
                            UPDATE tool_budgets
                            SET limit_count = %(limit_count)s, updated_at = %(updated_at)s
                            WHERE session_id = %(session_id)s
                              AND tool_type = %(tool_type)s
                            """,
                            {
                                "limit_count": current_limit,
                                "updated_at": now,
                                "session_id": safe_session,
                                "tool_type": safe_tool,
                            },
                        )
                else:
                    used = 0
                    current_limit = safe_limit
                    cur.execute(
                        """
                        INSERT INTO tool_budgets (
                            session_id, tool_type, used_count, limit_count, updated_at
                        ) VALUES (
                            %(session_id)s, %(tool_type)s, %(used_count)s, %(limit_count)s, %(updated_at)s
                        )
                        """,
                        {
                            "session_id": safe_session,
                            "tool_type": safe_tool,
                            "used_count": used,
                            "limit_count": current_limit,
                            "updated_at": now,
                        },
                    )

                allowed = (used + safe_consume) <= current_limit
                reason = None if allowed else f"budget exceeded: used={used}, consume={safe_consume}, limit={current_limit}"
                if allowed and not dry_run and safe_consume > 0:
                    used += safe_consume
                    cur.execute(
                        """
                        UPDATE tool_budgets
                        SET used_count = %(used_count)s, updated_at = %(updated_at)s
                        WHERE session_id = %(session_id)s
                          AND tool_type = %(tool_type)s
                        """,
                        {
                            "used_count": used,
                            "updated_at": now,
                            "session_id": safe_session,
                            "tool_type": safe_tool,
                        },
                    )
            conn.commit()
        remaining = max(0, current_limit - used)
        return {
            "session_id": safe_session,
            "tool_type": safe_tool,
            "limit": current_limit,
            "used": used,
            "remaining": remaining,
            "allowed": allowed,
            "reason": reason,
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Check tool budget failed: %s", exc)
        return {
            "session_id": safe_session,
            "tool_type": safe_tool,
            "limit": safe_limit,
            "used": 0,
            "remaining": safe_limit,
            "allowed": False,
            "reason": str(exc),
        }


def reset_tool_budget(*, session_id: str, tool_type: str) -> bool:
    safe_session = session_id.strip()[:120]
    safe_tool = tool_type.strip()[:80]
    if not safe_session or not safe_tool:
        return False
    try:
        with psycopg.connect(_database_url()) as conn:
            _ensure_tool_budgets_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM tool_budgets
                    WHERE session_id = %(session_id)s
                      AND tool_type = %(tool_type)s
                    """,
                    {
                        "session_id": safe_session,
                        "tool_type": safe_tool,
                    },
                )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Reset tool budget failed: %s", exc)
        return False
