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

    async def test_datei_suchen_erscheinen_in_jeder_guild(self):
        # Sie gehören zur Instanz, nicht zu einem Server — sonst wären sie in
        # /watch list unsichtbar und niemand käme auf /watch import.
        await sync_to_db(
            self.db, [file_search("Datei", "https://www.vinted.de/catalog?search_text=a")]
        )
        query = parse_search_url("https://www.vinted.de/catalog?search_text=b")
        await self.db.add_watch(
            guild_id=99,
            channel_id=1,
            creator_id=1,
            name="Command",
            query=query,
            source_url=query.web_url(),
            interval=60,
        )
        namen = [w.name for w in await self.db.list_watches(99)]
        self.assertEqual(sorted(namen), ["Command", "Datei"])
        # Ein fremder Server sieht die Datei-Suche, aber nicht die fremde.
        self.assertEqual([w.name for w in await self.db.list_watches(1234)], ["Datei"])

    async def test_uebernahme_einer_datei_suche(self):
        watches = await sync_to_db(
            self.db, [file_search("Nike", "https://www.vinted.de/catalog?search_text=nike")]
        )
        watch_id = watches[0].id
        await self.db.filter_new(watch_id, ["artikel-1"])

        await self.db.adopt_file_watch(
            watch_id, guild_id=42, channel_id=4711, creator_id=7
        )
        adopted = await self.db.get_watch(watch_id)
        assert adopted is not None
        self.assertEqual(adopted.origin, "command")
        self.assertEqual(adopted.channel_id, 4711)
        self.assertEqual(adopted.guild_id, 42)
        # Kein Webhook mehr: die Zustellung läuft ab jetzt über den Bot.
        self.assertEqual(adopted.webhook_url, "")
        # Historie bleibt — sonst gäbe es sofort einen Schwall alter Treffer.
        self.assertEqual(await self.db.filter_new(watch_id, ["artikel-1"]), set())
        # Und der Datei-Abgleich fasst sie nicht mehr an.
        self.assertEqual(await self.db.list_file_watches(), [])
        await sync_to_db(self.db, [])
        self.assertIsNotNone(await self.db.get_watch(watch_id))

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
                # Die Gruppenkennung wird aus der gespeicherten Abfrage
                # nachgetragen — sonst bliebe die Suche von der Entdopplung
                # ausgenommen und meldete weiter je Land einzeln.
                self.assertEqual(
                    watches[0].group_key,
                    watches[0].query.group_key(),
                )
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


class DedupeTests(DatabaseTestCase):
    """Derselbe Artikel darf nicht von jeder Länderkopie gemeldet werden.

    Vinted vergibt Artikel-IDs länderübergreifend — ohne Entdopplung liefert
    eine Suche in sieben Ländern sieben Alerts für denselben Fund.
    """

    URL = "https://www.vinted.de/catalog?search_text=nike&price_to=40"

    async def anlegen(self, host, *, name=None, text="nike"):
        query = parse_search_url(f"https://{host}/catalog?search_text={text}&price_to=40")
        return await self.db.add_watch(
            guild_id=1,
            channel_id=2,
            creator_id=3,
            name=name or host,
            query=query,
            source_url=query.web_url(),
            interval=60,
        )

    async def test_gruppenkennung_ist_ueber_laender_hinweg_gleich(self):
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        self.assertNotEqual(de.group_key, "")
        self.assertEqual(de.group_key, fr.group_key)

    async def test_andere_suche_hat_eine_andere_kennung(self):
        de = await self.anlegen("www.vinted.de")
        andere = await self.anlegen("www.vinted.de", text="carhartt", name="Carhartt")
        self.assertNotEqual(de.group_key, andere.group_key)

    async def test_zweites_land_meldet_denselben_artikel_nicht_nochmal(self):
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")

        erste = await self.db.filter_new(de.id, ["1", "2"], scope="group",
                                         group_key=de.group_key)
        self.assertEqual(erste, {"1", "2"})

        zweite = await self.db.filter_new(fr.id, ["1", "2", "3"], scope="group",
                                          group_key=fr.group_key)
        self.assertEqual(zweite, {"3"}, "1 und 2 hat Deutschland schon gemeldet")

    async def test_vermerkt_wird_trotzdem(self):
        # Unterdrückte IDs müssen für die Watch gespeichert sein, sonst hält sie
        # sich nach einem Neustart für ungeprimed und schluckt eine Runde.
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        await self.db.filter_new(de.id, ["1"], scope="group", group_key=de.group_key)
        await self.db.filter_new(fr.id, ["1"], scope="group", group_key=fr.group_key)
        self.assertTrue(await self.db.has_seen_any(fr.id))

    async def test_gruppe_schaltet_fremde_suchen_nicht_stumm(self):
        de = await self.anlegen("www.vinted.de")
        andere = await self.anlegen("www.vinted.de", text="carhartt", name="Carhartt")
        await self.db.filter_new(de.id, ["1"], scope="group", group_key=de.group_key)
        neu = await self.db.filter_new(andere.id, ["1"], scope="group",
                                       group_key=andere.group_key)
        self.assertEqual(neu, {"1"})

    async def test_scope_all_schaltet_alles_stumm(self):
        de = await self.anlegen("www.vinted.de")
        andere = await self.anlegen("www.vinted.de", text="carhartt", name="Carhartt")
        await self.db.filter_new(de.id, ["1"], scope="all")
        self.assertEqual(await self.db.filter_new(andere.id, ["1"], scope="all"), set())

    async def test_scope_watch_ist_das_alte_verhalten(self):
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        await self.db.filter_new(de.id, ["1"], scope="watch", group_key=de.group_key)
        neu = await self.db.filter_new(fr.id, ["1"], scope="watch", group_key=fr.group_key)
        self.assertEqual(neu, {"1"})

    async def test_dieselbe_watch_meldet_nie_doppelt(self):
        de = await self.anlegen("www.vinted.de")
        await self.db.filter_new(de.id, ["1"], scope="group", group_key=de.group_key)
        neu = await self.db.filter_new(de.id, ["1"], scope="group", group_key=de.group_key)
        self.assertEqual(neu, set())

    async def test_leere_kennung_entdoppelt_nicht(self):
        # Sicherheitsnetz: ohne Kennung darf nicht versehentlich alles als eine
        # große Gruppe behandelt werden.
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        await self.db.filter_new(de.id, ["1"], scope="group", group_key="")
        self.assertEqual(
            await self.db.filter_new(fr.id, ["1"], scope="group", group_key=""), {"1"}
        )

    async def test_leere_liste(self):
        de = await self.anlegen("www.vinted.de")
        self.assertEqual(
            await self.db.filter_new(de.id, [], scope="group", group_key=de.group_key),
            set(),
        )


class FingerprintDedupeTests(DatabaseTestCase):
    """Derselbe Artikel unter neuer ID darf nicht noch einmal gemeldet werden.

    Verkäufer löschen und stellen neu ein, um oben zu landen, oder legen
    denselben Pullover zweimal an. Die ID ist dann neu, der Artikel nicht.
    """

    async def anlegen(self, host="www.vinted.de", *, name=None, text="nike"):
        query = parse_search_url(f"https://{host}/catalog?search_text={text}")
        return await self.db.add_watch(
            guild_id=1, channel_id=2, creator_id=3, name=name or host,
            query=query, source_url=query.web_url(), interval=60,
        )

    async def test_neueinstellung_wird_nicht_nochmal_gemeldet(self):
        w = await self.anlegen()
        erste = await self.db.filter_new(w.id, ["1"], prints={"1": "abc"})
        self.assertEqual(erste, {"1"})
        zweite = await self.db.filter_new(w.id, ["2"], prints={"2": "abc"})
        self.assertEqual(zweite, set(), "gleicher Abdruck, neue ID")
        self.assertEqual(self.db.last_duplicates, 1)

    async def test_zaehlt_ueber_suchen_und_modi_hinweg(self):
        # Ein Artikel ist ein Artikel — auch wenn DEDUPE_SCOPE=watch die
        # ID-Entdopplung zwischen Suchen abschaltet.
        a = await self.anlegen(name="A")
        b = await self.anlegen(name="B", text="carhartt")
        await self.db.filter_new(a.id, ["1"], scope="watch", prints={"1": "abc"})
        neu = await self.db.filter_new(b.id, ["2"], scope="watch", prints={"2": "abc"})
        self.assertEqual(neu, set())

    async def test_gleicher_abdruck_im_selben_durchlauf(self):
        # Zweimal derselbe Pullover in einer Antwort: einer reicht.
        w = await self.anlegen()
        neu = await self.db.filter_new(
            w.id, ["1", "2", "3"], prints={"1": "abc", "2": "abc", "3": "xyz"}
        )
        self.assertEqual(neu, {"1", "3"})
        self.assertEqual(self.db.last_duplicates, 1)

    async def test_andere_abdruecke_kommen_durch(self):
        w = await self.anlegen()
        await self.db.filter_new(w.id, ["1"], prints={"1": "abc"})
        neu = await self.db.filter_new(w.id, ["2"], prints={"2": "def"})
        self.assertEqual(neu, {"2"})

    async def test_ohne_abdruck_keine_zusammenfassung(self):
        # Leerer Abdruck (kein Verkäufer bekannt) darf nichts zusammenfassen —
        # sonst würden alle verkäuferlosen Artikel zu einem.
        w = await self.anlegen()
        await self.db.filter_new(w.id, ["1"], prints={"1": ""})
        neu = await self.db.filter_new(w.id, ["2"], prints={"2": ""})
        self.assertEqual(neu, {"2"})

    async def test_dieselbe_id_ist_kein_verwandter(self):
        # Suche A meldet ID 1, Suche B sieht ID 1 mit demselben Abdruck:
        # im Modus watch darf B melden — der Abdruck gehört ja derselben ID.
        a = await self.anlegen(name="A")
        b = await self.anlegen(name="B", text="carhartt")
        await self.db.filter_new(a.id, ["1"], scope="watch", prints={"1": "abc"})
        neu = await self.db.filter_new(b.id, ["1"], scope="watch", prints={"1": "abc"})
        self.assertEqual(neu, {"1"})

    async def test_unterdrueckte_neueinstellung_bleibt_vermerkt(self):
        w = await self.anlegen()
        await self.db.filter_new(w.id, ["1"], prints={"1": "abc"})
        await self.db.filter_new(w.id, ["2"], prints={"2": "abc"})
        # Beim nächsten Durchlauf ist ID 2 bekannt — kein Alert, keine Zählung.
        self.assertEqual(await self.db.filter_new(w.id, ["2"], prints={"2": "abc"}), set())
        self.assertEqual(self.db.last_duplicates, 0)

    async def test_abdruecke_werden_mit_ausgeraeumt(self):
        w = await self.anlegen()
        await self.db.filter_new(w.id, ["1"], prints={"1": "abc"})
        await self.db.prune_seen(older_than_days=-1)
        neu = await self.db.filter_new(w.id, ["2"], prints={"2": "abc"})
        self.assertEqual(neu, {"2"})


class PriceSampleTests(DatabaseTestCase):
    """Die Vergleichsbasis, aus der „38 % unter Median" entsteht."""

    async def test_sammeln_und_lesen(self):
        await self.db.record_prices("g", [("1", 10.0, "EUR"), ("2", 20.0, "EUR")])
        self.assertEqual(sorted(await self.db.recent_prices("g", "EUR")), [10.0, 20.0])

    async def test_ein_artikel_zaehlt_nur_einmal(self):
        # Jeder Durchlauf liefert dieselben Artikel erneut — ohne das würde ein
        # lange stehendes Angebot den Median dominieren.
        await self.db.record_prices("g", [("1", 10.0, "EUR")])
        await self.db.record_prices("g", [("1", 99.0, "EUR")])
        self.assertEqual(await self.db.recent_prices("g", "EUR"), [10.0])

    async def test_waehrungen_werden_nicht_vermischt(self):
        # 40 PLN neben 40 EUR macht den Median unbrauchbar.
        await self.db.record_prices("g", [("1", 10.0, "EUR"), ("2", 200.0, "PLN")])
        self.assertEqual(await self.db.recent_prices("g", "EUR"), [10.0])
        self.assertEqual(await self.db.recent_prices("g", "PLN"), [200.0])

    async def test_suchen_teilen_sich_nichts(self):
        await self.db.record_prices("nike", [("1", 10.0, "EUR")])
        await self.db.record_prices("carhartt", [("2", 20.0, "EUR")])
        self.assertEqual(await self.db.recent_prices("nike", "EUR"), [10.0])

    async def test_laenderkopien_teilen_sich_die_basis(self):
        # Dieselbe Kennung heißt: Frankreich füttert Deutschlands Vergleichsbasis.
        # Genau so wird sie siebenmal so schnell belastbar.
        await self.db.record_prices("g", [("1", 10.0, "EUR")])
        await self.db.record_prices("g", [("2", 30.0, "EUR")])
        self.assertEqual(len(await self.db.recent_prices("g", "EUR")), 2)

    async def test_nullpreise_kommen_nicht_rein(self):
        await self.db.record_prices("g", [("1", 0.0, "EUR"), ("2", 5.0, "EUR")])
        self.assertEqual(await self.db.recent_prices("g", "EUR"), [5.0])

    async def test_ohne_kennung_wird_nichts_gesammelt(self):
        # Sicherheitsnetz: sonst landete alles in einem Topf "".
        await self.db.record_prices("", [("1", 10.0, "EUR")])
        self.assertEqual(await self.db.recent_prices("", "EUR"), [])

    async def test_altes_wird_ausgeraeumt(self):
        await self.db.record_prices("g", [("1", 10.0, "EUR")])
        # -1 Tage heißt „Stichtag in der Zukunft" und räumt damit auch den
        # gerade geschriebenen Eintrag ab; mit 0 läge er genau auf der Grenze.
        self.assertEqual(await self.db.prune_prices(older_than_days=-1), 1)
        self.assertEqual(await self.db.recent_prices("g", "EUR"), [])

    async def test_frisches_bleibt_beim_ausraeumen(self):
        await self.db.record_prices("g", [("1", 10.0, "EUR")])
        self.assertEqual(await self.db.prune_prices(older_than_days=60), 0)
        self.assertEqual(await self.db.recent_prices("g", "EUR"), [10.0])


class HeartbeatTests(DatabaseTestCase):
    """Das Lebenszeichen — daran erkennt der Sniper nach einem Absturz, wie
    lange er weg war."""

    async def test_beim_ersten_start_gibt_es_keins(self):
        self.assertIsNone(await self.db.last_heartbeat())

    async def test_setzen_und_lesen(self):
        await self.db.touch_heartbeat()
        import time
        self.assertAlmostEqual(await self.db.last_heartbeat(), int(time.time()), delta=2)

    async def test_ueberschreiben_statt_anhaeufen(self):
        await self.db.touch_heartbeat()
        await self.db.touch_heartbeat()
        async with self.db.conn.execute("SELECT COUNT(*) AS n FROM meta") as cursor:
            row = await cursor.fetchone()
        self.assertEqual(row["n"], 1)

    async def test_muell_wird_nicht_als_zeitstempel_gelesen(self):
        await self.db.set_meta("heartbeat", "irgendwas")
        self.assertIsNone(await self.db.last_heartbeat())

    async def test_meta_allgemein(self):
        self.assertIsNone(await self.db.get_meta("x"))
        await self.db.set_meta("x", "eins")
        await self.db.set_meta("x", "zwei")
        self.assertEqual(await self.db.get_meta("x"), "zwei")


class ContributionTests(DatabaseTestCase):
    """Trägt eine Länderkopie eigene Funde bei — oder nur Wiederholungen?

    Das ist die Zahl, an der sich entscheidet, ob sich sieben Länder lohnen
    oder ob Vinted die Artikel ohnehin länderübergreifend zeigt.
    """

    async def anlegen(self, host):
        query = parse_search_url(f"https://{host}/catalog?search_text=nike")
        return await self.db.add_watch(
            guild_id=1, channel_id=2, creator_id=3, name=host,
            query=query, source_url=query.web_url(), interval=60,
        )

    async def test_ohne_dubletten_null(self):
        de = await self.anlegen("www.vinted.de")
        await self.db.filter_new(de.id, ["1", "2"], scope="group",
                                 group_key=de.group_key)
        self.assertEqual(self.db.last_duplicates, 0)

    async def test_zaehlt_was_die_schwestersuche_schon_hatte(self):
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        await self.db.filter_new(de.id, ["1", "2"], scope="group",
                                 group_key=de.group_key)
        neu = await self.db.filter_new(fr.id, ["1", "2", "3"], scope="group",
                                       group_key=fr.group_key)
        self.assertEqual(neu, {"3"})
        self.assertEqual(self.db.last_duplicates, 2)

    async def test_wird_auf_der_watch_festgehalten(self):
        de = await self.anlegen("www.vinted.de")
        await self.db.mark_checked(de.id, error=None, new_hits=1, dupes=4)
        await self.db.mark_checked(de.id, error=None, new_hits=2, dupes=3)
        aktuell = await self.db.get_watch(de.id)
        self.assertEqual(aktuell.hits, 3)
        self.assertEqual(aktuell.dupes, 7)

    async def test_leere_abfrage_setzt_zurueck(self):
        # Sonst würde der Zähler des vorigen Durchlaufs erneut mitgeschrieben.
        de = await self.anlegen("www.vinted.de")
        fr = await self.anlegen("www.vinted.fr")
        await self.db.filter_new(de.id, ["1"], scope="group", group_key=de.group_key)
        await self.db.filter_new(fr.id, ["1"], scope="group", group_key=fr.group_key)
        self.assertEqual(self.db.last_duplicates, 1)
        await self.db.filter_new(fr.id, [], scope="group", group_key=fr.group_key)
        self.assertEqual(self.db.last_duplicates, 0)


if __name__ == "__main__":
    unittest.main()
