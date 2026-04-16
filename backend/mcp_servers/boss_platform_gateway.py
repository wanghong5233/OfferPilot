from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import uvicorn
from fastapi import Body, FastAPI, HTTPException

try:
    from . import _boss_platform_runtime as runtime
except Exception:  # pragma: no cover - fallback for direct script execution
    from backend.mcp_servers import _boss_platform_runtime as runtime


@dataclass(slots=True)
class _ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


# Sync Playwright 需要稳定线程上下文，使用单线程执行器避免跨线程切换。
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-mcp")


def _build_tools() -> dict[str, _ToolSpec]:
    return {
        "health": _ToolSpec(
            name="health",
            description="Return boss platform MCP runtime health",
            schema={"type": "object", "properties": {}},
            handler=lambda args: runtime.health(),
        ),
        "check_login": _ToolSpec(
            name="check_login",
            description="Validate BOSS login session",
            schema={
                "type": "object",
                "properties": {
                    "check_url": {"type": "string"},
                },
            },
            handler=lambda args: runtime.check_login(
                check_url=str(args.get("check_url") or "").strip(),
            ),
        ),
        "scan_jobs": _ToolSpec(
            name="scan_jobs",
            description="Scan jobs from BOSS sources",
            schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 80},
                    "max_pages": {"type": "integer", "minimum": 1, "maximum": 8},
                    "job_type": {"type": "string"},
                },
                "required": ["keyword"],
            },
            handler=lambda args: runtime.scan_jobs(
                keyword=str(args.get("keyword") or "").strip(),
                max_items=int(args.get("max_items") or 10),
                max_pages=int(args.get("max_pages") or 2),
                job_type=str(args.get("job_type") or "all"),
            ),
        ),
        "job_detail": _ToolSpec(
            name="job_detail",
            description="Fetch compact job detail payload",
            schema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "source_url": {"type": "string"},
                },
            },
            handler=lambda args: runtime.job_detail(
                job_id=str(args.get("job_id") or "").strip(),
                source_url=str(args.get("source_url") or "").strip(),
            ),
        ),
        "greet_job": _ToolSpec(
            name="greet_job",
            description="Trigger greet action (audit-first)",
            schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "source_url": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company": {"type": "string"},
                    "greeting_text": {"type": "string"},
                },
            },
            handler=lambda args: runtime.greet_job(
                run_id=str(args.get("run_id") or "").strip(),
                job_id=str(args.get("job_id") or "").strip(),
                source_url=str(args.get("source_url") or "").strip(),
                job_title=str(args.get("job_title") or "").strip(),
                company=str(args.get("company") or "").strip(),
                greeting_text=str(args.get("greeting_text") or "").strip(),
            ),
        ),
        "pull_conversations": _ToolSpec(
            name="pull_conversations",
            description="Pull conversation list",
            schema={
                "type": "object",
                "properties": {
                    "max_conversations": {"type": "integer", "minimum": 1, "maximum": 200},
                    "unread_only": {"type": "boolean"},
                    "fetch_latest_hr": {"type": "boolean"},
                    "chat_tab": {"type": "string"},
                },
            },
            handler=lambda args: runtime.pull_conversations(
                max_conversations=int(args.get("max_conversations") or 20),
                unread_only=bool(args.get("unread_only", False)),
                fetch_latest_hr=bool(args.get("fetch_latest_hr", True)),
                chat_tab=str(args.get("chat_tab") or "全部"),
            ),
        ),
        "reply_conversation": _ToolSpec(
            name="reply_conversation",
            description="Reply to one conversation",
            schema={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "reply_text": {"type": "string"},
                    "profile_id": {"type": "string"},
                    "conversation_hint": {"type": "object"},
                },
                "required": ["conversation_id", "reply_text"],
            },
            handler=lambda args: runtime.reply_conversation(
                conversation_id=str(args.get("conversation_id") or "").strip(),
                reply_text=str(args.get("reply_text") or "").strip(),
                profile_id=str(args.get("profile_id") or "default").strip() or "default",
                conversation_hint=dict(args.get("conversation_hint") or {})
                if isinstance(args.get("conversation_hint"), dict)
                else {},
            ),
        ),
        "mark_processed": _ToolSpec(
            name="mark_processed",
            description="Mark one conversation as processed",
            schema={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["conversation_id"],
            },
            handler=lambda args: runtime.mark_processed(
                conversation_id=str(args.get("conversation_id") or "").strip(),
                run_id=str(args.get("run_id") or "").strip(),
                note=str(args.get("note") or "").strip(),
            ),
        ),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Pulse Boss MCP Gateway", version="0.1.0")
    tools = _build_tools()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return runtime.health()

    @app.get("/tools")
    async def list_tools() -> dict[str, Any]:
        return {
            "tools": [
                {
                    "server": "boss",
                    "name": spec.name,
                    "description": spec.description,
                    "schema": spec.schema,
                }
                for spec in tools.values()
            ]
        }

    @app.post("/call")
    async def call_tool(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        spec = tools.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"tool not found: {name}")
        arguments = payload.get("arguments")
        safe_arguments = dict(arguments) if isinstance(arguments, dict) else {}
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(_TOOL_EXECUTOR, lambda: spec.handler(safe_arguments))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc
        return {"ok": True, "result": result}

    return app


app = create_app()


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(__import__("os").getenv("PULSE_BOSS_MCP_GATEWAY_PORT", "8811"))
    uvicorn.run(app, host=host, port=port)
