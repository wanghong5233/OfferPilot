from __future__ import annotations

import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from reportlab.lib.pagesizes import A4

from app.tz import now_beijing
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def _safe_part(text: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", text).strip("_")
    return (normalized[:48] or fallback).lower()


def _export_dir() -> Path:
    configured = os.getenv("MATERIAL_EXPORT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    backend_root = Path(__file__).resolve().parents[1]
    return (backend_root / "exports" / "materials").resolve()


def _format_lines(thread: dict[str, Any]) -> list[str]:
    draft = thread.get("draft") if isinstance(thread.get("draft"), dict) else {}
    bullets = draft.get("resume_bullets") if isinstance(draft.get("resume_bullets"), list) else []
    cover_letter = str(draft.get("cover_letter") or "")
    greeting = str(draft.get("greeting_message") or "")

    lines = [
        "OfferPilot Material Export",
        "",
        f"Thread ID: {thread.get('thread_id', '')}",
        f"Job ID: {thread.get('job_id', '')}",
        f"Title: {thread.get('title', '')}",
        f"Company: {thread.get('company', '')}",
        f"Resume Version: {thread.get('resume_version', '')}",
        f"Status: {thread.get('status', '')}",
        "",
        "=== Resume Bullets ===",
    ]
    if bullets:
        for idx, bullet in enumerate(bullets, start=1):
            lines.append(f"{idx}. {str(bullet)}")
    else:
        lines.append("(empty)")

    lines.extend(
        [
            "",
            "=== Cover Letter ===",
            cover_letter or "(empty)",
            "",
            "=== BOSS Greeting Message ===",
            greeting or "(empty)",
            "",
            f"Exported At: {now_beijing().isoformat(timespec='seconds')}",
        ]
    )
    return lines


def _render_pdf(lines: list[str], output_path: Path) -> None:
    pdf = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4
    x = 15 * mm
    y = height - 15 * mm
    line_height = 5.3 * mm

    pdf.setFont("Helvetica", 10)
    for raw in lines:
        wrapped = textwrap.wrap(raw, width=90) or [""]
        for line in wrapped:
            if y < 15 * mm:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = height - 15 * mm
            pdf.drawString(x, y, line)
            y -= line_height
    pdf.save()


def export_material_thread(
    thread: dict[str, Any],
    *,
    export_format: Literal["pdf", "txt"] = "pdf",
) -> tuple[str, str]:
    export_dir = _export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    title_part = _safe_part(str(thread.get("title") or ""), "job")
    company_part = _safe_part(str(thread.get("company") or ""), "company")
    stamp = now_beijing().strftime("%Y%m%d_%H%M%S")
    thread_id = str(thread.get("thread_id") or "thread")[:8]
    file_name = f"{stamp}_{thread_id}_{title_part}_{company_part}.{export_format}"
    output_path = export_dir / file_name

    lines = _format_lines(thread)
    if export_format == "txt":
        output_path.write_text("\n".join(lines), encoding="utf-8")
    else:
        _render_pdf(lines, output_path)
    return file_name, str(output_path)


def resolve_export_file(file_name: str) -> Path | None:
    base = os.path.basename(file_name)
    if base != file_name:
        return None
    path = (_export_dir() / base).resolve()
    export_root = _export_dir()
    try:
        path.relative_to(export_root)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path
