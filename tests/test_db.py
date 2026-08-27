"""Tests für die Datenbank und den Abgleich mit `searches.toml`.

Hier hängt der Nutzerzustand dran: welche Suchen laufen und welche Artikel schon
gemeldet wurden. Fehler an dieser Stelle bedeuten entweder verpasste Treffer
oder einen Schwall doppelter Alerts.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Damit der Stub auch bei `python -m unittest tests.test_db` gefunden wird und
# nicht nur bei `unittest discover -s tests`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()  # muss vor dem Import von vinted_sniper.db passieren

from vinted_sniper.db import Database, DatabaseUnavailable  # noqa: E402
from vinted_sniper.searches import FileSearch, sync_to_db  # noqa: E402
from vinted_sniper.vinted.urls import parse_search_url  # noqa: E402

WEBHOOK = "https://discord.com/api/webhooks/1/abc"


def file_search(name: str, url: str, *, interval: int = 60, webhook: str = WEBHOOK):
    query = parse_search_url(url)
    return FileSearch(
        name=name,
        query=query,
        interval=interval,
        webhook_url=webhook,
        source_url=query.web_url(),
    )


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "test.db")
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()


class WatchCrudTests(DatabaseTestCase):
    async def test_anlegen_und_wiederfinden(self):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=nike")
        watch = await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name="Nike",
            query=query,
            source_url=query.web_url(),
            interval=45,
        )
        self.assertEqual(watch.name, "Nike")
        self.assertEqual(watch.host, "www.vinted.de")
        self.assertEqual(watch.interval, 45)
        self.assertTrue(watch.enabled)
        self.assertEqual(watch.webhook_url, "")
        self.assertEqual(watch.origin, "command")

        # Die Filter überleben den Weg durch die Datenbank.
        self.assertEqual(watch.query.scalars["search_text"], "nike")

        again = await self.db.get_watch(watch.id)
        assert again is not None
        self.assertEqual(again.name, "Nike")

    async def test_guild_filter_trennt_server(self):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=a")
        for guild in (10, 10, 20):
            await self.db.add_watch(
                guild_id=guild,
                channel_id=1,
                creator_id=1,
                name=f"w{guild}-{id(query)}-{guild}",
                query=query,
                source_url=query.web_url(),
                interval=60,
            )
        self.assertEqual(len(await self.db.list_watches(10)), 2)
        self.assertEqual(len(await self.db.list_watches(20)), 1)
        self.assertEqual(len(await self.db.list_watches()), 3)

    async def test_pausieren_intervall_und_loeschen(self):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=nike")
        watch = await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name="Nike",
            query=query,
            source_url=query.web_url(),
            interval=60,
        )

        await self.db.set_enabled(watch.id, False)
        refreshed = await self.db.get_watch(watch.id)
        assert refreshed is not None
        self.assertFalse(refreshed.enabled)

        await self.db.set_interval(watch.id, 120)
        refreshed = await self.db.get_watch(watch.id)
        assert refreshed is not None
        self.assertEqual(refreshed.interval, 120)

        self.assertTrue(await self.db.delete_watch(watch.id))
        self.assertIsNone(await self.db.get_watch(watch.id))
        self.assertFalse(await self.db.delete_watch(watch.id))

    async def test_treffer_und_fehler_werden_mitgeschrieben(self):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=nike")
        watch = await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name="Nike",
            query=query,
            source_url=query.web_url(),
            interval=60,
        )
        await self.db.mark_checked(watch.id, error=None, new_hits=3)
        await self.db.mark_checked(watch.id, error="Antibot", new_hits=0)
        refreshed = await self.db.get_watch(watch.id)
        assert refreshed is not None
        self.assertEqual(refreshed.hits, 3)
        self.assertEqual(refreshed.last_error, "Antibot")
        self.assertIsNotNone(refreshed.last_checked_at)


class SeenItemTests(DatabaseTestCase):
    async def _watch(self, name: str = "Nike"):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=nike")
        return await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name=name,
            query=query,
            source_url=query.web_url(),
            interval=60,
        )

    async def test_nur_neue_ids_kommen_zurueck(self):
        watch = await self._watch()
        self.assertEqual(await self.db.filter_new(watch.id, ["a", "b"]), {"a", "b"})
        # Zweiter Aufruf: alles bekannt, nichts wird erneut gemeldet.
        self.assertEqual(await self.db.filter_new(watch.id, ["a", "b"]), set())
        self.assertEqual(await self.db.filter_new(watch.id, ["b", "c"]), {"c"})

    async def test_suchen_teilen_ihre_historie_nicht(self):
        first = await self._watch("Erste")
        second = await self._watch("Zweite")
        await self.db.filter_new(first.id, ["a"])
        # Dieselbe Artikel-ID ist für eine andere Suche weiterhin neu.
        self.assertEqual(await self.db.filter_new(second.id, ["a"]), {"a"})

    async def test_leere_liste(self):
        watch = await self._watch()
        self.assertEqual(await self.db.filter_new(watch.id, []), set())

    async def test_priming_zustand(self):
        watch = await self._watch()
        self.assertFalse(await self.db.has_seen_any(watch.id))
        await self.db.filter_new(watch.id, ["a"])
        self.assertTrue(await self.db.has_seen_any(watch.id))

    async def test_loeschen_raeumt_die_historie_mit_ab(self):
        watch = await self._watch()
        await self.db.filter_new(watch.id, ["a", "b"])
        await self.db.delete_watch(watch.id)
        self.assertFalse(await self.db.has_seen_any(watch.id))


class FileSyncTests(DatabaseTestCase):
    async def test_erster_durchlauf_legt_alles_an(self):
        watches = await sync_to_db(
            self.db,
            [
                file_search("Nike", "https://www.vinted.de/catalog?search_text=nike"),
                file_search("Carhartt", "https://www.vinted.fr/catalog?search_text=carhartt"),
            ],
        )
        self.assertEqual(len(watches), 2)
        self.assertTrue(all(w.origin == "file" for w in watches))
        self.assertTrue(all(w.webhook_url == WEBHOOK for w in watches))
        self.assertEqual(watches[1].host, "www.vinted.fr")

    async def test_zweiter_durchlauf_behaelt_id_und_historie(self):
        searches = [file_search("Nike", "https://www.vinted.de/catalog?search_text=nike")]
        first = await sync_to_db(self.db, searches)
        await self.db.filter_new(first[0].id, ["artikel-1"])

        # Neustart mit unveränderter Datei: dieselbe Suche, dieselbe ID.
        second = await sync_to_db(self.db, searches)
        self.assertEqual(second[0].id, first[0].id)
        # Entscheidend: der bereits gemeldete Artikel gilt weiter als gesehen,
        # sonst gäbe es nach jedem Neustart doppelte Alerts.
        self.assertEqual(await self.db.filter_new(second[0].id, ["artikel-1"]), set())

    async def test_geaenderte_url_aktualisiert_die_bestehende_suche(self):
        before = await sync_to_db(
            self.db, [file_search("Nike", "https://www.vinted.de/catalog?search_text=nike")]
        )
        after = await sync_to_db(
            self.db,
            [
                file_search(
                    "Nike",
                    "https://www.vinted.fr/catalog?search_text=nike&price_to=30",
                    interval=90,
                )
            ],
        )
        self.assertEqual(after[0].id, before[0].id)
        self.assertEqual(after[0].host, "www.vinted.fr")
        self.assertEqual(after[0].interval, 90)
        self.assertEqual(after[0].query.scalars["price_to"], "30")

    async def test_entfernte_suche_verschwindet(self):
        await sync_to_db(
            self.db,
            [
                file_search("Bleibt", "https://www.vinted.de/catalog?search_text=a"),
                file_search("Fliegt", "https://www.vinted.de/catalog?search_text=b"),
            ],
        )
        remaining = await sync_to_db(
            self.db, [file_search("Bleibt", "https://www.vinted.de/catalog?search_text=a")]
        )
        self.assertEqual([w.name for w in remaining], ["Bleibt"])
        self.assertEqual([w.name for w in await self.db.list_file_watches()], ["Bleibt"])

    async def test_datei_suchen_fassen_command_suchen_nicht_an(self):
        query = parse_search_url("https://www.vinted.de/catalog?search_text=manuell")
        manual = await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name="Manuell",
            query=query,
            source_url=query.web_url(),
            interval=60,
        )
        await sync_to_db(
            self.db, [file_search("Datei", "https://www.vinted.de/catalog?search_text=datei")]
        )
        # Die per Slash-Command angelegte Suche überlebt den Datei-Abgleich.
        self.assertIsNotNone(await self.db.get_watch(manual.id))
        self.assertEqual(len(await self.db.list_watches()), 2)
        self.assertEqual(len(await self.db.list_file_watches()), 1)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_alte_datenbank_bekommt_die_neuen_spalten(self):
        # Eine Datenbank aus der Version vor dem Webhook-Support.
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alt.db"
            legacy = sqlite3.connect(path)
            legacy.executescript(
                """
                CREATE TABLE watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    interval INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    last_checked_at INTEGER,
                    last_error TEXT,
                    hits INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO watches
                    (guild_id, channel_id, creator_id, name, host, source_url,
                     query_json, interval, created_at)
                VALUES (1, 2, 3, 'Alt', 'www.vinted.de', 'https://x',
                        '{"host": "www.vinted.de", "lists": {}, "scalars": {}}', 60, 0);
                """
            )
            legacy.commit()
            legacy.close()

            db = Database(path)
            await db.connect()
            try:
                watches = await db.list_watches()
                self.assertEqual(len(watches), 1)
                # Der bestehende Datensatz bleibt erhalten und bekommt Defaults.
                self.assertEqual(watches[0].name, "Alt")
                self.assertEqual(watches[0].webhook_url, "")
                self.assertEqual(watches[0].origin, "command")
            finally:
                await db.close()

    async def test_unbeschreibbarer_pfad_meldet_sich_verstaendlich(self):
        # Der Fall aus der Praxis: das Datenverzeichnis lässt sich nicht anlegen
        # bzw. beschreiben. Statt eines rohen SQLite-Stacktrace muss eine
        # Meldung kommen, die sagt, was zu tun ist.
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "data"
            blocker.write_text("ich bin eine Datei, kein Verzeichnis")
            db = Database(blocker / "sniper.db")
            with self.assertRaises(DatabaseUnavailable) as ctx:
                await db.connect()
        self.assertIn("lässt sich nicht öffnen", str(ctx.exception))
        self.assertIn("docker compose", str(ctx.exception))

    async def test_connect_ist_wiederholbar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wieder.db"
            for _ in range(2):
                db = Database(path)
                await db.connect()
                await db.close()
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
