from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

ToolRing = Literal["ring1_builtin", "ring2_module", "ring3_mcp"]
ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    ring: ToolRing = "ring1_builtin"
    schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


def tool(
    *,
    name: str | None = None,
    description: str = "",
    ring: ToolRing = "ring1_builtin",
    schema: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to attach ToolSpec metadata to a callable."""

    def _decorator(func: ToolHandler) -> ToolHandler:
        func_name = str(name or getattr(func, "__name__", "")).strip()
        if not func_name:
            raise ValueError("tool name must be non-empty")
        spec = ToolSpec(
            name=func_name,
            description=str(description or "").strip() or func_name,
            ring=ring,
            schema=dict(schema or {}),
            metadata=dict(metadata or {}),
        )
        setattr(func, "__pulse_tool_spec__", spec)
        return func

    return _decorator


class ToolRegistry:
    """Registry for built-in, module, and MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        handler: ToolHandler,
        description: str,
        ring: ToolRing = "ring1_builtin",
        schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_name = str(name or "").strip()
        if not safe_name:
            raise ValueError("tool name must be non-empty")
        if safe_name in self._tools:
            raise ValueError(f"duplicated tool name: {safe_name}")
        spec = ToolSpec(
            name=safe_name,
            description=str(description or "").strip() or safe_name,
            ring=ring,
            schema=dict(schema or {}),
            metadata=dict(metadata or {}),
        )
        self._tools[safe_name] = _RegisteredTool(spec=spec, handler=handler)

    def register_callable(self, func: ToolHandler) -> None:
        spec = getattr(func, "__pulse_tool_spec__", None)
        if not isinstance(spec, ToolSpec):
            raise ValueError("callable is missing @tool metadata")
        self.register(
            name=spec.name,
            handler=func,
            description=spec.description,
            ring=spec.ring,
            schema=spec.schema,
            metadata=spec.metadata,
        )

    def get(self, name: str) -> ToolSpec | None:
        item = self._tools.get(str(name or "").strip())
        if item is None:
            return None
        return item.spec

    def list_tools(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in sorted(self._tools.keys())]

    async def invoke(self, name: str, args: dict[str, Any] | None = None) -> Any:
        safe_name = str(name or "").strip()
        entry = self._tools.get(safe_name)
        if entry is None:
            raise KeyError(f"tool not found: {safe_name}")
        payload = dict(args or {})
        result = entry.handler(payload)
        if inspect.isawaitable(result):
            return await result
        return result
