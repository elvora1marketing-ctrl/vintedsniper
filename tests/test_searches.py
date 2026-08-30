"""Tests für `searches.toml` — im Webhook-Modus die einzige Eingabemöglichkeit.

Entsprechend wichtig ist, dass Fehler sauber gemeldet statt stillschweigend
geschluckt werden.
"""

import tempfile
import unittest
from pathlib import Path

from vinted_sniper.searches import InvalidSearchFile, load_searches

DEFAULTS = {
    "default_interval": 60,
    "min_interval": 20,
    "default_webhook": "https://discord.com/api/webhooks/1/abc",
}


def write(content: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".toml", delete=False, encoding="utf-8"
    )
    handle.write(content)
    handle.close()
    return Path(handle.name)


class LoadSearchesTests(unittest.TestCase):
    def test_minimale_datei(self):
        path = write(
            """
            [[search]]
            name = "Nike"
            url = "https://www.vinted.de/catalog?search_text=nike&price_to=60"
            """
        )
        searches = load_searches(path, **DEFAULTS)
        self.assertEqual(len(searches), 1)
        search = searches[0]
        self.assertEqual(search.name, "Nike")
        self.assertEqual(search.query.host, "www.vinted.de")
        self.assertEqual(search.query.scalars["price_to"], "60")
        self.assertEqual(search.interval, 60)
        self.assertEqual(search.webhook_url, DEFAULTS["default_webhook"])

    def test_mehrere_suchen_ueber_domains_hinweg(self):
        path = write(
            """
            [[search]]
            name = "DE"
            url = "https://www.vinted.de/catalog?search_text=nike"

            [[search]]
            name = "FR"
            url = "https://www.vinted.fr/catalog?search_text=carhartt"
            interval = 120
            """
        )
        searches = load_searches(path, **DEFAULTS)
        self.assertEqual([s.name for s in searches], ["DE", "FR"])
        self.assertEqual(searches[1].query.host, "www.vinted.fr")
        self.assertEqual(searches[1].interval, 120)

    def test_name_faellt_auf_suchbegriff_zurueck(self):
        path = write(
            """
            [[search]]
            url = "https://www.vinted.de/catalog?search_text=stone+island"
            """
        )
        self.assertEqual(load_searches(path, **DEFAULTS)[0].name, "stone island")

    def test_eigener_webhook_schlaegt_den_default(self):
        path = write(
            """
            [[search]]
            name = "Extra"
            url = "https://www.vinted.de/catalog?search_text=nike"
            webhook = "https://discord.com/api/webhooks/2/xyz"
            """
        )
        self.assertEqual(
            load_searches(path, **DEFAULTS)[0].webhook_url,
            "https://discord.com/api/webhooks/2/xyz",
        )

    def test_zu_kurzes_intervall_wird_angehoben(self):
        path = write(
            """
            [[search]]
            name = "Hektisch"
            url = "https://www.vinted.de/catalog?search_text=nike"
            interval = 2
            """
        )
        with self.assertLogs("vinted_sniper.searches", level="WARNING") as logs:
            searches = load_searches(path, **DEFAULTS)
        self.assertEqual(searches[0].interval, 20)
        self.assertIn("Minimum", logs.output[0])

    def test_fehlende_datei(self):
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(Path("/nicht/vorhanden.toml"), **DEFAULTS)
        self.assertIn("gibt es nicht", str(ctx.exception))

    def test_verzeichnis_statt_datei_wird_erklaert(self):
        # Die Docker-Falle: Bind-Mount auf eine Datei, die es noch nicht gab.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(InvalidSearchFile) as ctx:
                load_searches(Path(directory), **DEFAULTS)
        self.assertIn("Verzeichnis", str(ctx.exception))

    def test_kaputtes_toml(self):
        path = write("[[search]\nname = 'x'")
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("kein gültiges TOML", str(ctx.exception))

    def test_einfache_klammern_werden_erklaert(self):
        path = write(
            """
            [search]
            name = "Falsch"
            url = "https://www.vinted.de/catalog?search_text=nike"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("[[search]]", str(ctx.exception))

    def test_leere_datei(self):
        path = write("# nichts hier\n")
        with self.assertRaises(InvalidSearchFile):
            load_searches(path, **DEFAULTS)

    def test_fehlende_url(self):
        path = write(
            """
            [[search]]
            name = "Ohne URL"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("`url` fehlt", str(ctx.exception))

    def test_kaputte_url_nennt_die_position(self):
        path = write(
            """
            [[search]]
            name = "Gut"
            url = "https://www.vinted.de/catalog?search_text=nike"

            [[search]]
            name = "Schlecht"
            url = "https://www.ebay.de/sch/nike"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("Nr. 2", str(ctx.exception))

    def test_doppelter_name_wird_abgelehnt(self):
        # Der Name ist der Schlüssel zur Historie — doppelt vergeben würden sich
        # die Suchen gegenseitig überschreiben.
        path = write(
            """
            [[search]]
            name = "Doppelt"
            url = "https://www.vinted.de/catalog?search_text=a"

            [[search]]
            name = "Doppelt"
            url = "https://www.vinted.de/catalog?search_text=b"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("gibt es schon", str(ctx.exception))

    def test_ohne_jedes_alert_ziel(self):
        path = write(
            """
            [[search]]
            name = "Ziellos"
            url = "https://www.vinted.de/catalog?search_text=nike"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, default_interval=60, min_interval=20, default_webhook="")
        self.assertIn("ALERT_WEBHOOK_URL", str(ctx.exception))

    def test_intervall_muss_zahl_sein(self):
        path = write(
            """
            [[search]]
            name = "Text-Intervall"
            url = "https://www.vinted.de/catalog?search_text=nike"
            interval = "schnell"
            """
        )
        with self.assertRaises(InvalidSearchFile) as ctx:
            load_searches(path, **DEFAULTS)
        self.assertIn("Zahl", str(ctx.exception))


class ExampleFileTests(unittest.TestCase):
    def test_mitgelieferte_vorlage_ist_gueltig(self):
        # Eine Vorlage, die selbst nicht lädt, ist schlimmer als keine.
        example = Path(__file__).resolve().parent.parent / "searches.example.toml"
        searches = load_searches(example, **DEFAULTS)
        self.assertGreaterEqual(len(searches), 1)
        self.assertTrue(all(s.query.host.startswith("www.vinted.") for s in searches))


class ExtraCountriesTests(unittest.TestCase):
    """`EXTRA_COUNTRIES`: jede Suche läuft automatisch auch im Ausland."""

    DATEI = """
[[search]]
name = "Nike"
url = "https://www.vinted.de/catalog?search_text=nike&price_to=40"
"""

    def laden(self, laender):
        return load_searches(write(self.DATEI), **DEFAULTS, extra_countries=laender)

    def test_ohne_angabe_bleibt_es_bei_einer(self):
        self.assertEqual(len(self.laden(())), 1)

    def test_je_land_eine_eigene_suche(self):
        searches = self.laden(("www.vinted.fr", "www.vinted.it"))
        self.assertEqual(
            [s.query.host for s in searches],
            ["www.vinted.de", "www.vinted.fr", "www.vinted.it"],
        )

    def test_ausgangssuche_behaelt_ihren_namen(self):
        # Der Name ist der Schlüssel zur Historie — er darf sich nicht ändern,
        # bloß weil jemand Länder hinzuschaltet.
        searches = self.laden(("www.vinted.fr",))
        self.assertEqual(searches[0].name, "Nike")
        self.assertEqual(searches[1].name, "Nike 🇫🇷")

    def test_filter_werden_uebernommen(self):
        kopie = self.laden(("www.vinted.fr",))[1]
        self.assertEqual(kopie.query.scalars["search_text"], "nike")
        self.assertEqual(kopie.query.scalars["price_to"], "40")
        self.assertIn("vinted.fr", kopie.source_url)

    def test_ausgangsdomain_wird_nicht_verdoppelt(self):
        searches = self.laden(("www.vinted.de", "www.vinted.fr"))
        self.assertEqual(len(searches), 2)

    def test_namenskollision_ueberschreibt_keine_bestehende_suche(self):
        datei = write("""
[[search]]
name = "Nike"
url = "https://www.vinted.de/catalog?search_text=nike"

[[search]]
name = "Nike 🇫🇷"
url = "https://www.vinted.fr/catalog?search_text=etwas+anderes"
""")
        searches = load_searches(datei, **DEFAULTS, extra_countries=("www.vinted.fr",))
        namen = [s.name for s in searches]
        self.assertEqual(namen.count("Nike 🇫🇷"), 1)
        # Die selbst benannte Suche gewinnt, die Kopie wird verworfen.
        eigene = next(s for s in searches if s.name == "Nike 🇫🇷")
        self.assertEqual(eigene.query.scalars["search_text"], "etwas anderes")


if __name__ == "__main__":
    unittest.main()
