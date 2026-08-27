"""SQLite-Persistenz für Watches und bereits gemeldete Artikel."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .vinted.urls import SearchQuery

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    creator_id      INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    host            TEXT    NOT NULL,
    source_url      TEXT    NOT NULL,
    query_json      TEXT    NOT NULL,
    interval        INTEGER NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    last_checked_at INTEGER,
    last_error      TEXT,
    hits            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seen_items (
    watch_id INTEGER NOT NULL,
    item_id  TEXT    NOT NULL,
    seen_at  INTEGER NOT NULL,
    PRIMARY KEY (watch_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_items(seen_at);
CREATE INDEX IF NOT EXISTS idx_watch_guild ON watches(guild_id);
"""


@dataclass
class Watch:
    id: int
    guild_id: int
    channel_id: int
    creator_id: int
    name: str
    host: str
    source_url: str
    query_json: str
    interval: int
    enabled: bool
    created_at: int
    last_checked_at: int | None
    last_error: str | None
    hits: int

    @property
    def query(self) -> SearchQuery:
        raw = json.loads(self.query_json)
        return SearchQuery(
            host=raw["host"],
            lists=raw.get("lists", {}),
            scalars=raw.get("scalars", {}),
        )

    @staticmethod
    def _row_to_watch(row: aiosqlite.Row) -> "Watch":
        return Watch(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            host=row["host"],
            source_url=row["source_url"],
            query_json=row["query_json"],
            interval=row["interval"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            hits=row["hits"],
        )


def serialize_query(query: SearchQuery) -> str:
    return json.dumps(
        {"host": query.host, "lists": query.lists, "scalars": query.scalars},
        ensure_ascii=False,
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        # WAL überlebt harte Neustarts deutlich besser als der Default.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() wurde nicht aufgerufen.")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -------------------------------------------------------------- Watches

    async def add_watch(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        name: str,
        query: SearchQuery,
        source_url: str,
        interval: int,
    ) -> Watch:
        cursor = await self.conn.execute(
            """
            INSERT INTO watches
                (guild_id, channel_id, creator_id, name, host, source_url,
                 query_json, interval, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                guild_id,
                channel_id,
                creator_id,
                name,
                query.host,
                source_url,
                serialize_query(query),
                interval,
                int(time.time()),
            ),
        )
        await self.conn.commit()
        watch = await self.get_watch(cursor.lastrowid)
        assert watch is not None
        return watch

    async def get_watch(self, watch_id: int) -> Watch | None:
        async with self.conn.execute(
            "SELECT * FROM watches WHERE id = ?", (watch_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return Watch._row_to_watch(row) if row else None

    async def list_watches(self, guild_id: int | None = None) -> list[Watch]:
        sql = "SELECT * FROM watches ORDER BY id"
        args: tuple[int, ...] = ()
        if guild_id is not None:
            sql = "SELECT * FROM watches WHERE guild_id = ? ORDER BY id"
            args = (guild_id,)
        async with self.conn.execute(sql, args) as cursor:
            rows = await cursor.fetchall()
        return [Watch._row_to_watch(row) for row in rows]

    async def delete_watch(self, watch_id: int) -> bool:
        cursor = await self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        await self.conn.execute("DELETE FROM seen_items WHERE watch_id = ?", (watch_id,))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def set_enabled(self, watch_id: int, enabled: bool) -> bool:
        cursor = await self.conn.execute(
            "UPDATE watches SET enabled = ? WHERE id = ?", (int(enabled), watch_id)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def set_interval(self, watch_id: int, interval: int) -> bool:
        cursor = await self.conn.execute(
            "UPDATE watches SET interval = ? WHERE id = ?", (interval, watch_id)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_checked(self, watch_id: int, *, error: str | None, new_hits: int = 0) -> None:
        await self.conn.execute(
            """
            UPDATE watches
               SET last_checked_at = ?, last_error = ?, hits = hits + ?
             WHERE id = ?
            """,
            (int(time.time()), error, new_hits, watch_id),
        )
        await self.conn.commit()

    # ----------------------------------------------------------- Gesehene Items

    async def filter_new(self, watch_id: int, item_ids: list[str]) -> set[str]:
        """IDs zurückgeben, die für diese Watch noch nie gemeldet wurden.

        Die IDs werden dabei direkt als gesehen markiert, damit ein Absturz
        zwischen Prüfung und Versand keine Doppel-Alerts erzeugt.
        """
        if not item_ids:
            return set()

        placeholders = ",".join("?" for _ in item_ids)
        async with self.conn.execute(
            f"SELECT item_id FROM seen_items WHERE watch_id = ? AND item_id IN ({placeholders})",
            (watch_id, *item_ids),
        ) as cursor:
            known = {row["item_id"] for row in await cursor.fetchall()}

        fresh = [item_id for item_id in item_ids if item_id not in known]
        if fresh:
            now = int(time.time())
            await self.conn.executemany(
                "INSERT OR IGNORE INTO seen_items (watch_id, item_id, seen_at) VALUES (?, ?, ?)",
                [(watch_id, item_id, now) for item_id in fresh],
            )
            await self.conn.commit()
        return set(fresh)

    async def has_seen_any(self, watch_id: int) -> bool:
        """Hat diese Watch schon einmal Artikel erfasst?

        Entscheidet nach einem Neustart, ob der nächste Durchlauf als
        Ausgangsbestand gilt oder normal alerten darf — sonst verschluckt jeder
        Neustart die Artikel aus der Ausfallzeit.
        """
        async with self.conn.execute(
            "SELECT 1 FROM seen_items WHERE watch_id = ? LIMIT 1", (watch_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def prune_seen(self, older_than_days: int = 7) -> int:
        cutoff = int(time.time()) - older_than_days * 86_400
        cursor = await self.conn.execute("DELETE FROM seen_items WHERE seen_at < ?", (cutoff,))
        await self.conn.commit()
        return cursor.rowcount
