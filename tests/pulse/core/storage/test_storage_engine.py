from __future__ import annotations

from typing import Any

from pulse.core.storage.engine import DatabaseEngine


class _FakeCursor:
    def __init__(self, *, one: Any = None, all_rows: list[Any] | None = None) -> None:
        self.executed_sql: list[tuple[str, Any]] = []
        self._one = one
        self._all = all_rows or []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed_sql.append((sql, params))

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> list[Any]:
        return self._all


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = 0
        self.closed = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        self.closed += 1


def test_resolve_database_url_precedence(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://legacy")
    monkeypatch.setenv("PULSE_DATABASE_URL", "postgresql://pulse")
    assert DatabaseEngine.resolve_database_url() == "postgresql://pulse"


def test_execute_fetch_all_and_commit() -> None:
    fake_cursor = _FakeCursor(all_rows=[("a",), ("b",)])
    fake_conn = _FakeConnection(fake_cursor)
    engine = DatabaseEngine(database_url="postgresql://test", connect_factory=lambda _: fake_conn)

    rows = engine.execute("select * from t", fetch="all")

    assert rows == [("a",), ("b",)]
    assert fake_cursor.executed_sql == [("select * from t", None)]
    assert fake_conn.committed == 1
    assert fake_conn.closed == 1


def test_execute_fetch_one_without_commit() -> None:
    fake_cursor = _FakeCursor(one={"ok": True})
    fake_conn = _FakeConnection(fake_cursor)
    engine = DatabaseEngine(database_url="postgresql://test", connect_factory=lambda _: fake_conn)

    row = engine.execute("select 1", {"k": "v"}, fetch="one", commit=False)

    assert row == {"ok": True}
    assert fake_cursor.executed_sql == [("select 1", {"k": "v"})]
    assert fake_conn.committed == 0
    assert fake_conn.closed == 1
