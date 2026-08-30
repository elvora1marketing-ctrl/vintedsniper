"""Tests für den Sammel-Import mehrerer Such-URLs."""

import unittest

from vinted_sniper import bulk

NIKE = "https://www.vinted.de/catalog?search_text=nike+air+max"
CARHARTT = "https://www.vinted.de/catalog?search_text=carhartt&price_to=40"
FRANKREICH = "https://www.vinted.fr/catalog?search_text=stone+island"


class ParseImportTests(unittest.TestCase):
    def test_eine_url_je_zeile(self):
        plan = bulk.parse_import(f"{NIKE}\n{CARHARTT}\n{FRANKREICH}")
        self.assertEqual(len(plan.entries), 3)
        self.assertEqual(plan.problems, [])
        self.assertEqual([e.query.host for e in plan.entries],
                         ["www.vinted.de", "www.vinted.de", "www.vinted.fr"])

    def test_leerzeilen_und_kommentare_werden_uebersprungen(self):
        plan = bulk.parse_import(f"# meine Suchen\n\n{NIKE}\n\n   \n# Ende")
        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(plan.problems, [])

    def test_name_vor_dem_strich(self):
        plan = bulk.parse_import(f"Nike in 44 | {NIKE}")
        self.assertEqual(plan.entries[0].name, "Nike in 44")

    def test_ohne_namen_wird_der_suchbegriff_genommen(self):
        plan = bulk.parse_import(NIKE)
        self.assertEqual(plan.entries[0].name, "nike air max")

    def test_url_mit_strich_wird_nicht_zerlegt(self):
        # Ein `|` im Query-String darf nicht als Namenstrenner gelten, sonst
        # zerschneidet der Import die Adresse.
        roh = "https://www.vinted.de/catalog?search_text=a|b"
        plan = bulk.parse_import(roh)
        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(plan.entries[0].query.scalars["search_text"], "a|b")

    def test_beifang_beim_kopieren(self):
        # Spitze Klammern aus Chats, Aufzählungszeichen aus Notizen.
        plan = bulk.parse_import(f"- <{NIKE}>\n* \"{CARHARTT}\"")
        self.assertEqual(len(plan.entries), 2)
        self.assertEqual(plan.problems, [])

    def test_doppelte_zeilen_zaehlen_nur_einmal(self):
        plan = bulk.parse_import(f"{NIKE}\n{NIKE}\n{CARHARTT}")
        self.assertEqual(len(plan.entries), 2)
        self.assertEqual(plan.duplicates, 1)

    def test_gleiche_suche_andere_schreibweise_ist_ein_duplikat(self):
        # Nach dem Parsen zählt die kanonische Form, nicht der Rohtext.
        plan = bulk.parse_import(
            "https://www.vinted.de/catalog?search_text=nike+air+max\n"
            "https://www.vinted.de/catalog?search_text=nike+air+max&order=relevance"
        )
        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(plan.duplicates, 1)

    def test_kaputte_zeile_stoppt_den_rest_nicht(self):
        plan = bulk.parse_import(f"{NIKE}\nkein-link\n{CARHARTT}")
        self.assertEqual(len(plan.entries), 2)
        self.assertEqual(len(plan.problems), 1)
        self.assertEqual(plan.problems[0].line, 2)

    def test_fremde_domain_wird_abgelehnt(self):
        plan = bulk.parse_import("https://www.vinted.de.angreifer.com/catalog?search_text=x")
        self.assertEqual(plan.entries, [])
        self.assertEqual(len(plan.problems), 1)

    def test_leerer_text(self):
        plan = bulk.parse_import("")
        self.assertEqual(plan.entries, [])
        self.assertEqual(plan.problems, [])

    def test_obergrenze(self):
        viele = "\n".join(
            f"https://www.vinted.de/catalog?search_text=suche{i}"
            for i in range(bulk.MAX_LINES + 5)
        )
        plan = bulk.parse_import(viele)
        self.assertEqual(len(plan.entries), bulk.MAX_LINES)
        self.assertEqual(len(plan.problems), 5)

    def test_problem_beschreibung_kuerzt_lange_zeilen(self):
        problem = bulk.ImportProblem(3, "x" * 200, "kaputt")
        self.assertLess(len(problem.describe()), 90)
        self.assertIn("Zeile 3", problem.describe())


class SummarizeTests(unittest.TestCase):
    def test_nur_angelegte(self):
        plan = bulk.ImportPlan()
        self.assertEqual(bulk.summarize(plan, angelegt=3), "3 Suche(n) angelegt.")

    def test_alles_zusammen(self):
        plan = bulk.parse_import(f"{NIKE}\n{NIKE}\nmüll")
        text = bulk.summarize(plan, angelegt=1, bekannt=2)
        self.assertIn("1 Suche(n) angelegt", text)
        self.assertIn("2 schon vorhanden", text)
        self.assertIn("1 doppelt", text)
        self.assertIn("1 Zeile(n) fehlerhaft", text)


if __name__ == "__main__":
    unittest.main()
