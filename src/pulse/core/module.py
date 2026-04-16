from __future__ import annotations

import importlib
import json
import logging
import pkgutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, FastAPI

if TYPE_CHECKING:
    from .runtime import AgentRuntime

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], None]


def _preview_payload(value: object, *, max_chars: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars] + "...(truncated)"
    return text


class BaseModule(ABC):
    """Base contract for all Pulse business modules."""

    name: str = ""
    description: str = ""
    route_prefix: str | None = None
    tags: list[str] | None = None

    def __init__(self) -> None:
        self._event_emitter: EventEmitter | None = None
        self._runtime: AgentRuntime | None = None

    @abstractmethod
    def register_routes(self, router: APIRouter) -> None:
        """Register module routes into the provided router."""

    def on_startup(self) -> None:
        """Optional startup hook."""

    def on_shutdown(self) -> None:
        """Optional shutdown hook."""

    def handle_intent(
        self,
        intent: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Optional intent handling hook for channel ingress routing."""
        return None

    def bind_runtime(self, runtime: AgentRuntime | None) -> None:
        """Inject the AgentRuntime so the module can register patrol tasks
        during on_startup.  Called by server.py before on_startup."""
        self._runtime = runtime

    def bind_event_emitter(self, emitter: EventEmitter | None) -> None:
        self._event_emitter = emitter

    def emit_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._event_emitter is None:
            return
        try:
            self._event_emitter(event_type, dict(payload or {}))
        except Exception:
            logger.exception("module event emit failed: module=%s event_type=%s", self.name, event_type)

    def emit_stage_event(
        self,
        *,
        stage: str,
        status: str,
        trace_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        safe_trace_id = str(trace_id or "").strip() or f"trace_{uuid4().hex[:12]}"
        safe_stage = str(stage or "").strip() or "unknown"
        safe_status = str(status or "").strip() or "unknown"
        row = {
            "trace_id": safe_trace_id,
            "module": self.name,
            "stage": safe_stage,
            "status": safe_status,
        }
        row.update(dict(payload or {}))
        self.emit_event(f"module.{self.name}.{safe_stage}.{safe_status}", row)
        return safe_trace_id


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, BaseModule] = {}
        self._event_emitter: EventEmitter | None = None

    @property
    def modules(self) -> tuple[BaseModule, ...]:
        return tuple(self._modules.values())

    def register(self, module: BaseModule) -> None:
        if not module.name:
            raise ValueError("module.name must be non-empty")
        if module.name in self._modules:
            raise ValueError(f"duplicated module name: {module.name}")
        module.bind_event_emitter(self._event_emitter)
        self._modules[module.name] = module

    def bind_event_emitter(self, emitter: EventEmitter | None) -> None:
        self._event_emitter = emitter
        for module in self._modules.values():
            module.bind_event_emitter(emitter)

    def discover(self, package_name: str = "pulse.modules") -> list[BaseModule]:
        package = importlib.import_module(package_name)
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            return []

        discovered: list[BaseModule] = []
        for child in pkgutil.iter_modules(package_path):
            module_path = f"{package_name}.{child.name}.module"
            try:
                candidate_module = importlib.import_module(module_path)
            except ModuleNotFoundError as exc:
                if exc.name == module_path:
                    logger.debug("skip module without module.py: %s", module_path)
                    continue
                raise

            module = self._extract_module(candidate_module)
            if module is None:
                logger.warning("skip invalid module declaration: %s", module_path)
                continue
            self.register(module)
            discovered.append(module)
        return discovered

    def attach_to_app(self, app: FastAPI) -> None:
        for module in self._modules.values():
            prefix = module.route_prefix or f"/api/modules/{module.name}"
            tags = module.tags or [module.name]
            router = APIRouter(prefix=prefix, tags=tags)
            module.register_routes(router)
            app.include_router(router)

    def as_tools(self) -> list[dict[str, object]]:
        tools: list[dict[str, object]] = []
        for module in self._modules.values():
            tool_name = f"module.{module.name}"

            def _handler(payload: dict[str, object], current_module: BaseModule = module) -> object:
                import time
                safe_payload = dict(payload or {})
                intent = str(safe_payload.get("intent") or f"module.{current_module.name}")
                text = str(safe_payload.get("text") or "")
                metadata_raw = safe_payload.get("metadata")
                metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
                trace_id = str(metadata.get("trace_id") or "").strip() or f"trace_{uuid4().hex[:12]}"
                metadata["trace_id"] = trace_id
                current_module.emit_stage_event(
                    stage="intent",
                    status="started",
                    trace_id=trace_id,
                    payload={
                        "intent": intent,
                        "trigger_source": "module_tool",
                        "text_preview": _preview_payload(text, max_chars=220),
                    },
                )
                t0 = time.monotonic()
                try:
                    result = current_module.handle_intent(intent, text, metadata)
                except Exception as exc:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    current_module.emit_stage_event(
                        stage="intent",
                        status="failed",
                        trace_id=trace_id,
                        payload={
                            "intent": intent,
                            "trigger_source": "module_tool",
                            "error": str(exc)[:500],
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                    raise
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                self._record_pipeline_run(
                    module_name=current_module.name,
                    intent=intent,
                    result=result,
                    elapsed_ms=elapsed_ms,
                )
                status = "completed"
                if isinstance(result, dict) and result.get("ok") is False:
                    status = "failed"
                current_module.emit_stage_event(
                    stage="intent",
                    status=status,
                    trace_id=trace_id,
                    payload={
                        "intent": intent,
                        "trigger_source": "module_tool",
                        "elapsed_ms": elapsed_ms,
                        "result_preview": _preview_payload(result, max_chars=260),
                    },
                )
                return result

            tools.append(
                {
                    "name": tool_name,
                    "description": str(module.description or f"Module tool for {module.name}"),
                    "ring": "ring2_module",
                    "handler": _handler,
                    "metadata": {
                        "module_name": module.name,
                        "route_prefix": module.route_prefix or f"/api/modules/{module.name}",
                    },
                }
            )
        return tools

    @staticmethod
    def _record_pipeline_run(
        *,
        module_name: str,
        intent: str,
        result: object,
        elapsed_ms: int,
    ) -> None:
        try:
            import json
            import uuid
            from .storage.engine import DatabaseEngine
            db = DatabaseEngine()
            status = "ok"
            if isinstance(result, dict) and result.get("ok") is False:
                status = "error"
            output_json = "{}"
            if isinstance(result, dict):
                output_json = json.dumps(result, ensure_ascii=False, default=str)[:4000]
            finished_at = datetime.now(timezone.utc)
            started_at = finished_at - timedelta(milliseconds=max(0, int(elapsed_ms)))
            db.execute(
                """INSERT INTO pipeline_runs(id, module_name, trigger_source, input_json, output_json, status, started_at, finished_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)""",
                (uuid.uuid4().hex, module_name, intent, "{}", output_json, status, started_at, finished_at),
            )
        except Exception as exc:
            logger.warning("failed to record pipeline run for %s: %s", module_name, exc)

    @staticmethod
    def _extract_module(candidate_module: ModuleType) -> BaseModule | None:
        direct = getattr(candidate_module, "module", None)
        if isinstance(direct, BaseModule):
            return direct

        factory = getattr(candidate_module, "get_module", None)
        if callable(factory):
            module = factory()
            if not isinstance(module, BaseModule):
                raise TypeError("get_module() must return BaseModule")
            return module
        return None
