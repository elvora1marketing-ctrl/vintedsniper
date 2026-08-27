"""Minimaler `aiosqlite`-Ersatz auf Basis von `sqlite3`.

`aiosqlite` lässt sich in dieser Umgebung nicht installieren, die Datenbank- und
Sync-Logik ist aber der Teil, der den Nutzerzustand hält — der soll nicht
ungetestet bleiben. Der Stub bildet genau die API-Ausschnitte nach, die
`vinted_sniper.db` benutzt: `execute` ist sowohl awaitable als auch
async-Kontextmanager, `Row` ist `sqlite3.Row`.

Nur für Tests. Die echte Anwendung nutzt das richtige `aiosqlite`.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from typing import Any, Iterable

Row = sqlite3.Row


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    async def fetchone(self) -> Any:
        return self._cursor.fetchone()

    async def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    async def __aenter__(self) -> "_Cursor":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _Result:
    """Ergebnis von `execute` — awaitable *und* async-Kontextmanager."""

    def __init__(self, run: Any) -> None:
        self._run = run
        self._cursor: _Cursor | None = None

    def _materialize(self) -> _Cursor:
        if self._cursor is None:
            self._cursor = _Cursor(self._run())
        return self._cursor

    def __await__(self) -> Any:
        async def go() -> _Cursor:
            return self._materialize()

        return go().__await__()

    async def __aenter__(self) -> _Cursor:
        return self._materialize()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class Connection:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self.row_factory: Any = None

    def _apply_row_factory(self) -> None:
        self._conn.row_factory = self.row_factory

    def execute(self, sql: str, args: Iterable[Any] = ()) -> _Result:
        def run() -> sqlite3.Cursor:
            self._apply_row_factory()
            return self._conn.execute(sql, tuple(args))

        return _Result(run)

    def executemany(self, sql: str, args: Iterable[Iterable[Any]]) -> _Result:
        rows = [tuple(row) for row in args]

        def run() -> sqlite3.Cursor:
            self._apply_row_factory()
            return self._conn.executemany(sql, rows)

        return _Result(run)

    def executescript(self, script: str) -> _Result:
        def run() -> sqlite3.Cursor:
            self._apply_row_factory()
            return self._conn.executescript(script)

        return _Result(run)

    async def commit(self) -> None:
        self._conn.commit()

    async def close(self) -> None:
        self._conn.close()


class _Connector:
    def __init__(self, path: str) -> None:
        self._path = path

    def __await__(self) -> Any:
        async def go() -> Connection:
            return Connection(self._path)

        return go().__await__()


def connect(path: Any) -> _Connector:
    return _Connector(str(path))


def install() -> None:
    """Den Stub als `aiosqlite` registrieren — vor dem Import von `vinted_sniper.db`."""
    if "aiosqlite" in sys.modules:
        return
    module = types.ModuleType("aiosqlite")
    module.connect = connect  # type: ignore[attr-defined]
    module.Connection = Connection  # type: ignore[attr-defined]
    module.Row = Row  # type: ignore[attr-defined]
    sys.modules["aiosqlite"] = module
