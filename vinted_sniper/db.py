"""SQLite-Persistenz für Watches und bereits gemeldete Artikel."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .vinted.urls import SearchQuery


class DatabaseUnavailable(RuntimeError):
    """Die Datenbankdatei lässt sich nicht anlegen oder öffnen."""

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
    hits            INTEGER NOT NULL DEFAULT 0,
    -- Eigenes Alert-Ziel; leer = über den Bot in `channel_id`.
    webhook_url     TEXT    NOT NULL DEFAULT '',
    -- 'command' = per /watch add angelegt, 'file' = aus searches.toml.
    origin          TEXT    NOT NULL DEFAULT 'command',
    -- Gemeinsame Kennung aller Länderkopien einer Suche. Vinted vergibt
    -- Artikel-IDs länderübergreifend; ohne die würde derselbe Fund von jeder
    -- Länderkopie einzeln gemeldet.
    group_key       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS seen_items (
    watch_id INTEGER NOT NULL,
    item_id  TEXT    NOT NULL,
    seen_at  INTEGER NOT NULL,
    PRIMARY KEY (watch_id, item_id)
);

-- Preise aller gesehenen Artikel je Suchgruppe. Daraus entsteht die
-- Vergleichsbasis, gegen die ein neuer Fund gehalten wird. Der Primärschlüssel
-- sorgt dafür, dass ein Artikel nur einmal zählt, auch wenn ihn jeder
-- Durchlauf erneut liefert.
CREATE TABLE IF NOT EXISTS price_samples (
    group_key TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    price     REAL NOT NULL,
    currency  TEXT NOT NULL,
    seen_at   INTEGER NOT NULL,
    PRIMARY KEY (group_key, item_id)
);

CREATE INDEX IF NOT EXISTS idx_price_lookup
    ON price_samples(group_key, currency, seen_at);

-- Kleiner Schlüssel-Wert-Speicher für Betriebszustand, allen voran das
-- Lebenszeichen. Nach einem Absturz lässt sich daran ablesen, wie lange der
-- Sniper weg war — der Prozess selbst weiß das nach dem Neustart nicht mehr.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_items(seen_at);
-- Für die Entdopplung über Watches hinweg: dort wird nach item_id allein
-- gesucht, der Primärschlüssel (watch_id, item_id) hilft dabei nicht.
CREATE INDEX IF NOT EXISTS idx_seen_item ON seen_items(item_id);
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
    webhook_url: str = ""
    origin: str = "command"
    group_key: str = ""

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
            webhook_url=row["webhook_url"] or "",
            origin=row["origin"] or "command",
            group_key=row["group_key"] or "",
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
        # Serialisiert Prüfen-und-Vermerken in `filter_new`. Ohne das könnten
        # zwei Länderkopien im selben Moment feststellen, dass ein Artikel noch
        # niemandem aufgefallen ist — und ihn beide melden.
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.path)
        except (OSError, sqlite3.Error) as exc:
            # Der mit Abstand häufigste Fall: das Datenverzeichnis gehört root,
            # der Container läuft aber als `sniper`. Ein roher SQLite-Stacktrace
            # hilft dabei niemandem weiter.
            raise DatabaseUnavailable(
                f"Datenbank {self.path} lässt sich nicht öffnen: {exc}\n"
                "Meist fehlen die Schreibrechte auf dem Datenverzeichnis. "
                "Behebung:\n"
                "  docker compose down && docker volume rm vintedsniper_sniper-data "
                "; docker compose up -d"
            ) from exc
        self._conn.row_factory = aiosqlite.Row
        # WAL überlebt harte Neustarts deutlich besser als der Default.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Fehlende Spalten nachrüsten, ohne bestehende Daten anzufassen.

        `CREATE TABLE IF NOT EXISTS` lässt eine bereits vorhandene Tabelle in
        Ruhe — eine Datenbank aus einer älteren Version hätte sonst die neuen
        Spalten nie.
        """
        assert self._conn is not None
        async with self._conn.execute("PRAGMA table_info(watches)") as cursor:
            existing = {row["name"] for row in await cursor.fetchall()}

        for column, ddl in (
            ("webhook_url", "ALTER TABLE watches ADD COLUMN webhook_url TEXT NOT NULL DEFAULT ''"),
            ("origin", "ALTER TABLE watches ADD COLUMN origin TEXT NOT NULL DEFAULT 'command'"),
            ("group_key", "ALTER TABLE watches ADD COLUMN group_key TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                await self._conn.execute(ddl)

        # Erst hier, nicht im Schema: bei einer Datenbank aus einer älteren
        # Version gibt es die Spalte vorher noch gar nicht, und ein Index auf
        # eine fehlende Spalte lässt den Start scheitern.
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watch_group ON watches(group_key)"
        )
        await self._backfill_group_keys()

    async def _backfill_group_keys(self) -> None:
        """Bestehenden Suchen ihre Gruppenkennung nachtragen.

        Sie lässt sich aus der gespeicherten Abfrage errechnen — ohne das
        blieben Suchen aus einer älteren Version von der Entdopplung
        ausgenommen und würden weiter je Land einzeln melden.
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id, query_json FROM watches WHERE group_key = ''"
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return
        for row in rows:
            raw = json.loads(row["query_json"])
            query = SearchQuery(
                host=raw["host"],
                lists=raw.get("lists", {}),
                scalars=raw.get("scalars", {}),
            )
            await self._conn.execute(
                "UPDATE watches SET group_key = ? WHERE id = ?",
                (query.group_key(), row["id"]),
            )

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
        webhook_url: str = "",
        origin: str = "command",
    ) -> Watch:
        cursor = await self.conn.execute(
            """
            INSERT INTO watches
                (guild_id, channel_id, creator_id, name, host, source_url,
                 query_json, interval, enabled, created_at, webhook_url, origin,
                 group_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
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
                webhook_url,
                origin,
                query.group_key(),
            ),
        )
        await self.conn.commit()
        watch = await self.get_watch(cursor.lastrowid)
        assert watch is not None
        return watch

    async def update_file_watch(
        self,
        watch_id: int,
        *,
        query: SearchQuery,
        source_url: str,
        interval: int,
        webhook_url: str,
    ) -> None:
        """Eine aus der Datei stammende Suche an die geänderte Datei angleichen."""
        await self.conn.execute(
            """
            UPDATE watches
               SET host = ?, source_url = ?, query_json = ?, interval = ?,
                   webhook_url = ?, group_key = ?, enabled = 1
             WHERE id = ?
            """,
            (
                query.host,
                source_url,
                serialize_query(query),
                interval,
                webhook_url,
                query.group_key(),
                watch_id,
            ),
        )
        await self.conn.commit()

    async def adopt_file_watch(
        self, watch_id: int, *, guild_id: int, channel_id: int, creator_id: int
    ) -> None:
        """Eine Datei-Suche in eine per Command verwaltete Suche umwandeln.

        Sie behält ID und Trefferhistorie — es gibt also keinen Alert-Schwall —,
        wird aber ab sofort über den Bot in den gewählten Channel zugestellt und
        beim nächsten Abgleich nicht mehr aus `searches.toml` überschrieben.
        """
        await self.conn.execute(
            """
            UPDATE watches
               SET origin = 'command', webhook_url = '',
                   guild_id = ?, channel_id = ?, creator_id = ?
             WHERE id = ? AND origin = 'file'
            """,
            (guild_id, channel_id, creator_id, watch_id),
        )
        await self.conn.commit()

    async def list_file_watches(self) -> list[Watch]:
        async with self.conn.execute(
            "SELECT * FROM watches WHERE origin = 'file' ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
        return [Watch._row_to_watch(row) for row in rows]

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
            # Suchen aus Datei und Panel gehören zur Instanz, nicht zu einem
            # Server. Ohne sie hier wären sie in `/watch list` unsichtbar —
            # und niemand käme auf die Idee, sie zu übernehmen.
            sql = (
                "SELECT * FROM watches "
                "WHERE guild_id = ? OR origin IN ('file', 'panel') "
                "ORDER BY id"
            )
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

    async def _seen_elsewhere(
        self, watch_id: int, item_ids: list[str], *, scope: str, group_key: str
    ) -> set[str]:
        """IDs, die eine **andere** Watch schon gemeldet hat.

        `scope="group"` beschränkt das auf die Länderkopien derselben Suche,
        `scope="all"` auf sämtliche Suchen.
        """
        if not item_ids:
            return set()
        placeholders = ",".join("?" for _ in item_ids)

        if scope == "group":
            if not group_key:
                return set()
            sql = (
                "SELECT s.item_id FROM seen_items s "
                "JOIN watches w ON w.id = s.watch_id "
                f"WHERE w.group_key = ? AND s.watch_id != ? AND s.item_id IN ({placeholders})"
            )
            args: tuple[object, ...] = (group_key, watch_id, *item_ids)
        else:
            sql = (
                "SELECT item_id FROM seen_items "
                f"WHERE watch_id != ? AND item_id IN ({placeholders})"
            )
            args = (watch_id, *item_ids)

        async with self.conn.execute(sql, args) as cursor:
            return {row["item_id"] for row in await cursor.fetchall()}

    async def filter_new(
        self,
        watch_id: int,
        item_ids: list[str],
        *,
        scope: str = "watch",
        group_key: str = "",
    ) -> set[str]:
        """IDs zurückgeben, die gemeldet werden sollen.

        Neu für diese Watch sind sie in jedem Fall, und sie werden sofort als
        gesehen vermerkt — ein Absturz zwischen Prüfung und Versand darf keine
        Doppel-Alerts erzeugen.

        Bei `scope="group"` oder `"all"` fallen zusätzlich die IDs weg, die
        eine andere Watch bereits gemeldet hat: Vinted vergibt Artikel-IDs
        länderübergreifend, ohne das meldet jede Länderkopie denselben Fund.
        Vermerkt bleiben sie trotzdem — sonst hielte sich diese Watch nach
        einem Neustart für ungeprimed und würde eine Runde stumm schlucken.

        Die Sperre serialisiert Prüfen und Vermerken. Zwei Länderkopien, die
        im selben Moment abfragen, würden sonst beide „noch niemand hat's
        gemeldet" sehen und beide alerten.
        """
        if not item_ids:
            return set()

        async with self._write_lock:
            placeholders = ",".join("?" for _ in item_ids)
            async with self.conn.execute(
                f"SELECT item_id FROM seen_items WHERE watch_id = ? "
                f"AND item_id IN ({placeholders})",
                (watch_id, *item_ids),
            ) as cursor:
                known = {row["item_id"] for row in await cursor.fetchall()}

            fresh = [item_id for item_id in item_ids if item_id not in known]
            if not fresh:
                return set()

            anderswo: set[str] = set()
            if scope in ("group", "all"):
                anderswo = await self._seen_elsewhere(
                    watch_id, fresh, scope=scope, group_key=group_key
                )

            now = int(time.time())
            await self.conn.executemany(
                "INSERT OR IGNORE INTO seen_items (watch_id, item_id, seen_at) VALUES (?, ?, ?)",
                [(watch_id, item_id, now) for item_id in fresh],
            )
            await self.conn.commit()

        return {item_id for item_id in fresh if item_id not in anderswo}

    # ------------------------------------------------------- Betriebszustand

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def get_meta(self, key: str) -> str | None:
        async with self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["value"] if row else None

    async def touch_heartbeat(self) -> None:
        await self.set_meta("heartbeat", str(int(time.time())))

    async def last_heartbeat(self) -> int | None:
        """Wann der Sniper zuletzt gelebt hat.

        `None` beim allerersten Start — dann gab es keine Lücke, sondern nur
        noch keinen Betrieb.
        """
        roh = await self.get_meta("heartbeat")
        if roh is None or not roh.isdigit():
            return None
        return int(roh)

    # -------------------------------------------------------------- Preise

    async def record_prices(
        self, group_key: str, samples: list[tuple[str, float, str]]
    ) -> None:
        """Preise gesehener Artikel in die Vergleichsbasis aufnehmen.

        Aufgerufen wird das mit **allen** Artikeln eines Durchlaufs, nicht nur
        den neuen: die Vergleichsbasis soll den Markt abbilden, nicht nur die
        Zugänge. `INSERT OR IGNORE` sorgt dafür, dass jeder Artikel trotzdem
        genau einmal zählt — mit dem Preis, zu dem er zuerst gesehen wurde.
        """
        if not group_key or not samples:
            return
        now = int(time.time())
        await self.conn.executemany(
            "INSERT OR IGNORE INTO price_samples "
            "(group_key, item_id, price, currency, seen_at) VALUES (?, ?, ?, ?, ?)",
            [
                (group_key, item_id, price, currency, now)
                for item_id, price, currency in samples
                if price > 0
            ],
        )
        await self.conn.commit()

    async def recent_prices(
        self, group_key: str, currency: str, *, days: int = 30, limit: int = 500
    ) -> list[float]:
        """Vergleichspreise einer Suchgruppe.

        Nur dieselbe Währung: 40 PLN neben 40 EUR würde den Median unbrauchbar
        machen. Und nur aus dem gewählten Zeitfenster — Preise von vor einem
        halben Jahr sagen über den heutigen Markt wenig.
        """
        if not group_key:
            return []
        cutoff = int(time.time()) - days * 86_400
        async with self.conn.execute(
            "SELECT price FROM price_samples "
            "WHERE group_key = ? AND currency = ? AND seen_at >= ? "
            "ORDER BY seen_at DESC LIMIT ?",
            (group_key, currency, cutoff, limit),
        ) as cursor:
            return [row["price"] for row in await cursor.fetchall()]

    async def prune_prices(self, older_than_days: int = 60) -> int:
        cutoff = int(time.time()) - older_than_days * 86_400
        cursor = await self.conn.execute(
            "DELETE FROM price_samples WHERE seen_at < ?", (cutoff,)
        )
        await self.conn.commit()
        return cursor.rowcount

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
