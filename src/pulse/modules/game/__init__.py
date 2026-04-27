"""Game automation domain package.

Single deterministic-workflow module for low-frequency personal game
automation. Games are declared in ``games/<id>.yaml``; adding another game is
a config + template change, not a new Pulse module.
"""

from .module import GameModule, get_module
from .skill import SKILL_SCHEMA

__all__ = ["GameModule", "SKILL_SCHEMA", "get_module"]
