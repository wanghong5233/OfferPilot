from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Literal

FetchMode = Literal["none", "one", "all"]
ConnectFactory = Callable[[str], Any]


class DatabaseEngine:
    """Thin database engine wrapper for PostgreSQL connections."""

    def __init__(self, *, database_url: str | None = None, connect_factory: ConnectFactory | None = None) -> None:
        self._database_url = database_url or self.resolve_database_url()
        self._connect_factory = connect_factory

    @property
    def database_url(self) -> str:
        return self._database_url

    @staticmethod
    def resolve_database_url() -> str:
        value = os.getenv("PULSE_DATABASE_URL") or os.getenv("DATABASE_URL")
        if not str(value or "").strip():
            raise RuntimeError("DATABASE_URL is required for Pulse storage engine")
        return str(value).strip()

    def _connect(self, dsn: str) -> Any:
        if self._connect_factory is not None:
            return self._connect_factory(dsn)
        import psycopg

        return psycopg.connect(dsn)

    @contextmanager
    def connection(self) -> Any:
        conn = self._connect(self._database_url)
        try:
            yield conn
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | tuple[Any, ...] | None = None,
        *,
        fetch: FetchMode = "none",
        commit: bool = True,
    ) -> Any:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
                else:
                    result = None
            if commit:
                conn.commit()
            return result
