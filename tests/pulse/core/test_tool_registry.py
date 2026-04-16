from __future__ import annotations

import asyncio

from pulse.core.tool import ToolRegistry, tool


@tool(name="demo.echo", description="Echo input")
def _echo_tool(args: dict[str, object]) -> dict[str, object]:
    return {"echo": args}


def test_tool_registry_register_and_invoke() -> None:
    registry = ToolRegistry()
    registry.register_callable(_echo_tool)
    tools = registry.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "demo.echo"

    result = asyncio.run(registry.invoke("demo.echo", {"x": 1}))
    assert result == {"echo": {"x": 1}}


def test_tool_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register_callable(_echo_tool)
    try:
        registry.register_callable(_echo_tool)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
