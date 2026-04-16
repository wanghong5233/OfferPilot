from __future__ import annotations

from fastmcp import FastMCP

try:
    from . import _boss_platform_runtime as runtime
except Exception:  # pragma: no cover - fallback for direct script execution
    from backend.mcp_servers import _boss_platform_runtime as runtime

_MCP = FastMCP("boss-platform")


@_MCP.tool
def health() -> dict:
    """Return boss platform MCP runtime health."""
    return runtime.health()


@_MCP.tool
def check_login(check_url: str = "") -> dict:
    """Validate BOSS login session via browser profile."""
    return runtime.check_login(check_url=check_url)


@_MCP.tool
def scan_jobs(keyword: str, max_items: int = 10, max_pages: int = 2, job_type: str = "all") -> dict:
    """Scan jobs from configured BOSS sources."""
    return runtime.scan_jobs(
        keyword=keyword,
        max_items=max_items,
        max_pages=max_pages,
        job_type=job_type,
    )


@_MCP.tool
def job_detail(job_id: str = "", source_url: str = "") -> dict:
    """Fetch a compact job detail payload."""
    return runtime.job_detail(job_id=job_id, source_url=source_url)


@_MCP.tool
def greet_job(
    run_id: str = "",
    job_id: str = "",
    source_url: str = "",
    job_title: str = "",
    company: str = "",
    greeting_text: str = "",
) -> dict:
    """Trigger greet action (audit-first, executor pluggable)."""
    return runtime.greet_job(
        run_id=run_id,
        job_id=job_id,
        source_url=source_url,
        job_title=job_title,
        company=company,
        greeting_text=greeting_text,
    )


@_MCP.tool
def pull_conversations(
    max_conversations: int = 20,
    unread_only: bool = False,
    fetch_latest_hr: bool = True,
    chat_tab: str = "全部",
) -> dict:
    """Pull conversation list from source inbox."""
    return runtime.pull_conversations(
        max_conversations=max_conversations,
        unread_only=unread_only,
        fetch_latest_hr=fetch_latest_hr,
        chat_tab=chat_tab,
    )


@_MCP.tool
def reply_conversation(
    conversation_id: str,
    reply_text: str,
    profile_id: str = "default",
    conversation_hint: dict | None = None,
) -> dict:
    """Reply to one conversation with profile context."""
    return runtime.reply_conversation(
        conversation_id=conversation_id,
        reply_text=reply_text,
        profile_id=profile_id,
        conversation_hint=dict(conversation_hint or {}),
    )


@_MCP.tool
def mark_processed(conversation_id: str, run_id: str = "", note: str = "") -> dict:
    """Mark a conversation processed."""
    return runtime.mark_processed(
        conversation_id=conversation_id,
        run_id=run_id,
        note=note,
    )


if __name__ == "__main__":
    _MCP.run()
