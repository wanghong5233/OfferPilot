from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from .schemas import MaterialDraft
from .workflow import _invoke_structured, _load_candidate_context


def generate_material_draft(
    *,
    title: str,
    company: str,
    skills: list[str],
    jd_raw: str,
    resume_version: str,
    feedback: str | None = None,
) -> MaterialDraft:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a job application material generator. "
                "Return concise, practical outputs for internship applications.",
            ),
            (
                "human",
                "Job title: {title}\n"
                "Company: {company}\n"
                "Required skills: {skills}\n"
                "JD raw text:\n{jd_raw}\n\n"
                "Candidate profile:\n{resume_profile}\n\n"
                "Resume version: {resume_version}\n"
                "Feedback for regeneration (if any): {feedback}\n\n"
                "Generate:\n"
                "1) 3-5 resume bullets (each <= 80 words)\n"
                "2) cover_letter (<= 220 words)\n"
                "3) greeting_message for BOSS/HR chat (<= 120 chars)\n",
            ),
        ]
    ).invoke(
        {
            "title": title,
            "company": company,
            "skills": ", ".join(skills),
            "jd_raw": jd_raw[:6000],
            "resume_profile": _load_candidate_context(),
            "resume_version": resume_version,
            "feedback": feedback or "None",
        }
    )
    return _invoke_structured(
        prompt,
        MaterialDraft,
        route="material_draft",
    )


def build_material_summary(draft: MaterialDraft) -> str:
    bullets = " | ".join(draft.resume_bullets[:3])
    return f"bullets={bullets}; greeting={draft.greeting_message[:120]}"


def extract_skills_from_job(job: dict[str, Any]) -> list[str]:
    parsed = job.get("jd_parsed")
    if isinstance(parsed, dict):
        raw = parsed.get("skills")
        if isinstance(raw, list):
            return [str(v).strip() for v in raw if str(v).strip()]
    return []
