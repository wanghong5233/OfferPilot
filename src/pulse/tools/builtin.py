"""Ring 1 built-in tools — thin re-export from individual tool files."""
from __future__ import annotations

from ..core.tool import ToolRegistry
from .alarm import alarm_create
from .flight import flight_search
from .weather import weather_current
from .web import web_search_tool


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register_callable(alarm_create)
    registry.register_callable(weather_current)
    registry.register_callable(flight_search)
    registry.register_callable(web_search_tool)
