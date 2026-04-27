"""Game driver connector package."""

from .base import GameDriver
from .registry import build_driver

__all__ = ["GameDriver", "build_driver"]
