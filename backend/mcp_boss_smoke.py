"""Compatibility wrapper for legacy command.

Use `python -m backend.mcp_boss_acceptance` for the canonical acceptance check.
"""

try:
    from .mcp_boss_acceptance import main
except Exception:  # pragma: no cover - fallback for direct script execution
    from backend.mcp_boss_acceptance import main


if __name__ == "__main__":
    main()
