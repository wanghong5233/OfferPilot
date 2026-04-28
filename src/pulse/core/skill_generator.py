from __future__ import annotations

import importlib.util
import json
import re
import threading
import types
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sandbox import PythonSandbox, SandboxScanResult
from .tokenizer import token_preview
from .tool import ToolRegistry, ToolSpec


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_output_dir(raw_path: str | None) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return (_repo_root() / "generated" / "skills").resolve()
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (_repo_root() / candidate).resolve()


def _slug(value: str, *, fallback: str = "generated_skill") -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    if not safe:
        return fallback
    if safe[0].isdigit():
        safe = f"s_{safe}"
    return safe


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillGenerator:
    """Generate, scan, persist, and hot-load runtime tools.

    Architecture spec flow: LLM analysis -> code generation -> AST safety scan -> sandbox test -> hot loading.
    """

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        output_dir: str | None = None,
        sandbox: PythonSandbox | None = None,
        llm_router: Any | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._sandbox = sandbox or PythonSandbox()
        self._output_dir = _resolve_output_dir(output_dir)
        self._index_path = self._output_dir / "index.json"
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._llm_router = llm_router
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._auto_register_active_skills()

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def list_skills(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._records.values())
        if status:
            safe_status = str(status).strip().lower()
            rows = [item for item in rows if str(item.get("status") or "").lower() == safe_status]
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return [deepcopy(item) for item in rows]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        safe_id = str(skill_id or "").strip()
        if not safe_id:
            return None
        with self._lock:
            row = self._records.get(safe_id)
        return deepcopy(row) if row is not None else None

    def create_skill(
        self,
        *,
        prompt: str,
        tool_name: str | None = None,
        description: str | None = None,
        code_override: str | None = None,
    ) -> dict[str, Any]:
        safe_prompt = str(prompt or "").strip()
        if not safe_prompt:
            raise ValueError("prompt is required")
        requested_name = str(tool_name or "").strip()
        base_name = _slug(requested_name or self._infer_tool_name(safe_prompt))
        unique_name = self._allocate_unique_tool_name(base_name)
        safe_description = str(description or "").strip() or f"Generated skill for: {safe_prompt[:80]}"
        source_code = str(code_override or "").strip() or self._render_tool_code(
            tool_name=unique_name,
            description=safe_description,
            prompt=safe_prompt,
        )

        scan = self._sandbox.validate(source_code)
        skill_id = f"sk_{uuid.uuid4().hex[:12]}"
        file_name = f"{skill_id}_{unique_name}.py"
        file_path = (self._output_dir / file_name).resolve()
        status = "blocked" if not scan.allowed else "draft"
        if scan.allowed:
            file_path.write_text(source_code, encoding="utf-8")

        record = {
            "skill_id": skill_id,
            "tool_name": unique_name,
            "description": safe_description,
            "prompt": safe_prompt,
            "status": status,
            "activation_required": True,
            "file_path": str(file_path),
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "scan": scan.to_dict(),
            "code_preview": source_code[:300],
        }
        with self._lock:
            self._records[skill_id] = record
            self._save_index()
        return deepcopy(record)

    def activate_skill(self, *, skill_id: str, confirm: bool) -> dict[str, Any]:
        safe_id = str(skill_id or "").strip()
        if not safe_id:
            raise ValueError("skill_id is required")
        with self._lock:
            record = deepcopy(self._records.get(safe_id))
        if record is None:
            raise KeyError(f"skill not found: {safe_id}")
        if not confirm:
            return {
                "ok": False,
                "skill_id": safe_id,
                "needs_confirmation": True,
                "status": record.get("status"),
            }
        if str(record.get("status") or "").lower() == "blocked":
            return {"ok": False, "skill_id": safe_id, "error": "skill is blocked by sandbox"}

        file_path = Path(str(record.get("file_path") or "")).expanduser()
        if not file_path.is_absolute():
            file_path = (_repo_root() / file_path).resolve()
        if not file_path.is_file():
            return {"ok": False, "skill_id": safe_id, "error": "generated file does not exist"}

        source = file_path.read_text(encoding="utf-8")
        scan = self._sandbox.validate(source)
        if not scan.allowed:
            record["status"] = "blocked"
            record["updated_at"] = _utc_now_iso()
            record["scan"] = scan.to_dict()
            with self._lock:
                self._records[safe_id] = record
                self._save_index()
            return {"ok": False, "skill_id": safe_id, "error": "sandbox validation failed", "scan": scan.to_dict()}

        module_name = f"pulse_generated_{safe_id}"
        module = self._load_module(file_path=file_path, module_name=module_name)
        callables = self._collect_tool_callables(module)
        if not callables:
            return {"ok": False, "skill_id": safe_id, "error": "no @tool callable found"}

        activated_tools: list[str] = []
        for func in callables:
            spec = getattr(func, "__pulse_tool_spec__", None)
            if not isinstance(spec, ToolSpec):
                continue
            existing = self._tool_registry.get(spec.name)
            if existing is None:
                self._tool_registry.register_callable(func)
            activated_tools.append(spec.name)

        record["status"] = "active"
        record["updated_at"] = _utc_now_iso()
        record["activated_at"] = _utc_now_iso()
        record["activated_tools"] = list(activated_tools)
        record["scan"] = scan.to_dict()
        with self._lock:
            self._records[safe_id] = record
            self._save_index()
        return {
            "ok": True,
            "skill_id": safe_id,
            "status": "active",
            "activated_tools": activated_tools,
        }

    def _auto_register_active_skills(self) -> None:
        rows = self.list_skills(status="active")
        for row in rows:
            file_path = Path(str(row.get("file_path") or "")).expanduser()
            if not file_path.is_absolute():
                file_path = (_repo_root() / file_path).resolve()
            if not file_path.is_file():
                continue
            try:
                module = self._load_module(file_path=file_path, module_name=f"pulse_generated_{row['skill_id']}")
                for func in self._collect_tool_callables(module):
                    spec = getattr(func, "__pulse_tool_spec__", None)
                    if not isinstance(spec, ToolSpec):
                        continue
                    if self._tool_registry.get(spec.name) is None:
                        self._tool_registry.register_callable(func)
            except Exception:
                continue

    def _load_module(self, *, file_path: Path, module_name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load module spec: {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _collect_tool_callables(module: types.ModuleType) -> list[Any]:
        rows: list[Any] = []
        for value in vars(module).values():
            if not callable(value):
                continue
            spec = getattr(value, "__pulse_tool_spec__", None)
            if isinstance(spec, ToolSpec):
                rows.append(value)
        return rows

    def _allocate_unique_tool_name(self, base_name: str) -> str:
        safe_base = _slug(base_name)
        with self._lock:
            existing_record_names = {str(row.get("tool_name") or "") for row in self._records.values()}
        candidate = safe_base
        idx = 1
        while self._tool_registry.get(candidate) is not None or candidate in existing_record_names:
            idx += 1
            candidate = f"{safe_base}_{idx}"
        return candidate

    def _infer_tool_name(self, prompt: str) -> str:
        if self._llm_router is not None:
            try:
                instruction = (
                    "Given the following user request for a tool, infer a concise snake_case tool name "
                    "(max 30 chars, lowercase, only a-z0-9_). Return ONLY the name, nothing else.\n\n"
                    f"Request: {token_preview(prompt, max_tokens=160)}"
                )
                raw = self._llm_router.invoke_text(instruction, route="classification").strip()
                name = re.sub(r"[^a-z0-9_]", "", raw.lower().strip("` \n"))
                if name and len(name) <= 30:
                    return name
            except Exception:
                pass
        words = re.findall(r"[a-zA-Z0-9_]+", str(prompt or "").lower())
        return "_".join(words[:3]) if words else "generated_skill"

    def _render_tool_code(self, *, tool_name: str, description: str, prompt: str) -> str:
        if self._llm_router is None:
            raise RuntimeError("Skill generation requires llm_router; placeholder generation is disabled.")
        try:
            return self._generate_code_with_llm(
                tool_name=tool_name, description=description, prompt=prompt,
            )
        except Exception as exc:
            raise RuntimeError(f"LLM code generation failed: {exc}") from exc

    def _generate_code_with_llm(self, *, tool_name: str, description: str, prompt: str) -> str:
        safe_name = _slug(tool_name)
        system = (
            "You are a Python code generator for the Pulse AI assistant.\n"
            "Generate a SINGLE Python file that implements a tool.\n\n"
            "Requirements:\n"
            "1. Start with: from __future__ import annotations\n"
            "2. Import: from pulse.core.tool import tool\n"
            "3. Use the @tool decorator with name, description, schema, ring='ring1_builtin'\n"
            "4. The function must accept args: dict[str, Any] and return dict[str, Any]\n"
            "5. For HTTP calls use urllib.request (stdlib only)\n"
            "6. NO dangerous operations: no os.system, subprocess, eval, exec, __import__\n"
            "7. Handle errors with try/except, return error info in the dict\n"
            "8. Include type annotations\n\n"
            "Return ONLY valid Python code. No markdown fences, no explanations."
        )
        user = (
            f"Tool name: {safe_name}\n"
            f"Description: {description}\n"
            f"User request: {prompt}\n\n"
            f"Generate the Python implementation:"
        )
        from langchain_core.messages import HumanMessage as HM, SystemMessage as SM
        raw = self._llm_router.invoke_text([SM(content=system), HM(content=user)], route="generation")
        code = raw.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            code = "\n".join(lines[start:end]).strip()
        if "from pulse.core.tool import tool" not in code:
            code = "from pulse.core.tool import tool\n" + code
        if "from __future__" not in code:
            code = "from __future__ import annotations\n\n" + code
        return code

    @staticmethod
    def _render_fallback_code(*, tool_name: str, description: str, prompt: str) -> str:
        raise RuntimeError("Skill placeholder generation is disabled.")

    def _load_index(self) -> None:
        path = self._index_path
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        raw_rows = payload.get("skills") if isinstance(payload, dict) else []
        if not isinstance(raw_rows, list):
            return
        rows: dict[str, dict[str, Any]] = {}
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id") or "").strip()
            if not skill_id:
                continue
            rows[skill_id] = dict(item)
        with self._lock:
            self._records = rows

    def _save_index(self) -> None:
        rows = sorted(self._records.values(), key=lambda row: str(row.get("created_at") or ""))
        payload = {"skills": rows}
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
