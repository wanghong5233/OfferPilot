from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from .base import BaseChannelAdapter, IncomingMessage


class CliChannelAdapter(BaseChannelAdapter):
    name = "cli"

    def parse_incoming(self, payload: Any) -> IncomingMessage | None:
        text = str(payload or "").strip()
        if not text:
            return None
        return IncomingMessage(
            channel=self.name,
            user_id="local-user",
            text=text,
            metadata={"source": "stdin"},
            received_at=datetime.now(timezone.utc),
        )

    def run_interactive_loop(
        self,
        *,
        input_func: Callable[[str], str] = input,
        prompt: str = "pulse> ",
        stop_words: tuple[str, ...] = ("exit", "quit"),
    ) -> int:
        """Simple local loop for manual message ingress testing."""
        count = 0
        while True:
            raw = input_func(prompt)
            if raw.strip().lower() in stop_words:
                break
            message = self.parse_incoming(raw)
            if message is None:
                continue
            dispatched = self.dispatch(message)
            if inspect.isawaitable(dispatched):
                asyncio.run(dispatched)
            count += 1
        return count
