from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn

from .core.config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="pulse", description="Pulse command line interface")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start Pulse API server")
    start.add_argument("--host", default=settings.host, help="Bind host")
    start.add_argument("--port", type=int, default=settings.port, help="Bind port")
    start.add_argument(
        "--reload",
        action="store_true",
        default=settings.reload,
        help="Enable auto-reload in development mode",
    )
    start.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    return parser


def _cmd_start(args: argparse.Namespace) -> int:
    uvicorn.run(
        "pulse.core.server:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "start":
        return _cmd_start(args)
    parser.error(f"unsupported command: {args.command}")
    return 2
