from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _normalize_module(name: str) -> str:
    return str(name or "").strip().split(".")[0]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _call_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


@dataclass(slots=True)
class SandboxScanResult:
    allowed: bool
    issues: list[str] = field(default_factory=list)
    blocked_imports: list[str] = field(default_factory=list)
    blocked_calls: list[str] = field(default_factory=list)
    syntax_error: str | None = None
    subprocess_ok: bool = False
    subprocess_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "issues": list(self.issues),
            "blocked_imports": list(self.blocked_imports),
            "blocked_calls": list(self.blocked_calls),
            "syntax_error": self.syntax_error,
            "subprocess_ok": self.subprocess_ok,
            "subprocess_error": self.subprocess_error,
        }


@dataclass(slots=True)
class SandboxPolicy:
    blocked_import_roots: set[str] = field(
        default_factory=lambda: {
            "os",
            "subprocess",
            "socket",
            "ctypes",
            "multiprocessing",
            "pathlib",
            "shutil",
            "requests",
            "httpx",
        }
    )
    blocked_calls: set[str] = field(
        default_factory=lambda: {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "input",
            "breakpoint",
            "os.system",
            "os.popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.Popen",
            "subprocess.check_output",
        }
    )
    max_source_chars: int = 20000
    max_ast_nodes: int = 6000
    subprocess_timeout_sec: float = 5.0


class PythonSandbox:
    """AST + subprocess-based safety checks for generated code."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()

    def scan(self, source_code: str) -> SandboxScanResult:
        source = str(source_code or "")
        issues: list[str] = []
        blocked_imports: list[str] = []
        blocked_calls: list[str] = []

        if len(source) > self._policy.max_source_chars:
            issues.append(f"source too large: {len(source)} > {self._policy.max_source_chars}")
            return SandboxScanResult(
                allowed=False,
                issues=issues,
                blocked_imports=[],
                blocked_calls=[],
                syntax_error=None,
            )

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return SandboxScanResult(
                allowed=False,
                issues=[f"syntax error: {exc.msg}"],
                blocked_imports=[],
                blocked_calls=[],
                syntax_error=f"{exc.msg} (line {exc.lineno})",
            )

        total_nodes = sum(1 for _ in ast.walk(tree))
        if total_nodes > self._policy.max_ast_nodes:
            issues.append(f"ast too large: {total_nodes} > {self._policy.max_ast_nodes}")
            return SandboxScanResult(
                allowed=False,
                issues=issues,
                blocked_imports=[],
                blocked_calls=[],
                syntax_error=None,
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = _normalize_module(alias.name)
                    if root in self._policy.blocked_import_roots:
                        blocked_imports.append(alias.name)
                        issues.append(f"blocked import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module_root = _normalize_module(node.module or "")
                if module_root in self._policy.blocked_import_roots:
                    blocked_imports.append(str(node.module or ""))
                    issues.append(f"blocked import-from: {node.module}")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if not name:
                    continue
                if name in self._policy.blocked_calls:
                    blocked_calls.append(name)
                    issues.append(f"blocked call: {name}")

        return SandboxScanResult(
            allowed=not issues,
            issues=issues,
            blocked_imports=sorted(set(blocked_imports)),
            blocked_calls=sorted(set(blocked_calls)),
            syntax_error=None,
        )

    def validate(self, source_code: str) -> SandboxScanResult:
        result = self.scan(source_code)
        if not result.allowed:
            return result
        subprocess_ok, subprocess_error = self._verify_in_subprocess(source_code)
        result.subprocess_ok = subprocess_ok
        result.subprocess_error = subprocess_error
        if not subprocess_ok:
            result.allowed = False
            if subprocess_error:
                result.issues.append(subprocess_error)
        return result

    def _verify_in_subprocess(self, source_code: str) -> tuple[bool, str | None]:
        temp_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".py",
                prefix="pulse_skill_",
                delete=False,
            ) as handle:
                handle.write(str(source_code or ""))
                temp_file = Path(handle.name)

            command = [sys.executable, "-m", "py_compile", str(temp_file)]
            completed = subprocess.run(  # noqa: S603
                command,  # noqa: S607
                capture_output=True,
                text=True,
                timeout=max(0.5, float(self._policy.subprocess_timeout_sec)),
            )
            if completed.returncode == 0:
                return True, None
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            message = stderr or stdout or "subprocess compile failed"
            return False, f"subprocess check failed: {message[:300]}"
        except subprocess.TimeoutExpired:
            return False, "subprocess check timeout"
        except Exception as exc:
            return False, f"subprocess check error: {str(exc)[:300]}"
        finally:
            if temp_file is not None and temp_file.exists():
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
