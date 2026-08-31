"""Tests für die Preisreferenz.

Hier entscheidet sich, ob ein Alert Geld wert ist oder nur Rauschen. Zwei
Fehlerarten sind teuer: ein Schnäppchen nicht melden, und einen normalen Preis
als Schnäppchen ausgeben.
"""

import unittest

from vinted_sniper import pricing

# Ein realistischer Markt: Median liegt bei 40.
MARKT = [20.0, 25.0, 30.0, 35.0, 38.0, 40.0, 42.0, 45.0, 50.0, 55.0, 60.0] * 3


class StatsTests(unittest.TestCase):
    def test_median_und_anzahl(self):
        stats = pricing.stats_from(MARKT)
        self.assertEqual(stats.median, 40.0)
        self.assertEqual(stats.count, len(MARKT))
        self.assertEqual(stats.cheapest, 20.0)

    def test_leere_liste(self):
        self.assertIsNone(pricing.stats_from([]))

    def test_nullpreise_zaehlen_nicht(self):
        # Vinted liefert gelegentlich 0 — das würde den Median nach unten ziehen
        # und alles als teuer erscheinen lassen.
        stats = pricing.stats_from([0.0, 0.0, 10.0, 20.0, 30.0])
        self.assertEqual(stats.count, 3)
        self.assertEqual(stats.median, 20.0)

    def test_nur_nullpreise(self):
        self.assertIsNone(pricing.stats_from([0.0, 0.0]))

    def test_zu_wenige_werte_sind_nicht_belastbar(self):
        self.assertFalse(pricing.stats_from([10.0, 20.0, 30.0]).reliable)

    def test_genug_werte_sind_belastbar(self):
        self.assertTrue(pricing.stats_from(MARKT).reliable)


class DiscountTests(unittest.TestCase):
    def setUp(self):
        self.stats = pricing.stats_from(MARKT)

    def test_haelfte_des_medians(self):
        self.assertAlmostEqual(pricing.discount(20.0, self.stats), 50.0)

    def test_ueber_dem_median(self):
        self.assertAlmostEqual(pricing.discount(60.0, self.stats), -50.0)

    def test_auf_dem_median(self):
        self.assertAlmostEqual(pricing.discount(40.0, self.stats), 0.0)

    def test_ohne_datenlage_kein_urteil(self):
        duenn = pricing.stats_from([10.0, 20.0])
        self.assertIsNone(pricing.discount(5.0, duenn))
        self.assertIsNone(pricing.discount(5.0, None))

    def test_ohne_preis_kein_urteil(self):
        self.assertIsNone(pricing.discount(None, self.stats))
        self.assertIsNone(pricing.discount(0.0, self.stats))


class IsDealTests(unittest.TestCase):
    def test_ohne_schwelle_ist_alles_meldenswert(self):
        self.assertTrue(pricing.is_deal(-80.0, 0))
        self.assertTrue(pricing.is_deal(None, 0))

    def test_schwelle_greift(self):
        self.assertTrue(pricing.is_deal(40.0, 30))
        self.assertTrue(pricing.is_deal(30.0, 30))
        self.assertFalse(pricing.is_deal(29.9, 30))
        self.assertFalse(pricing.is_deal(-10.0, 30))

    def test_ohne_datenlage_wird_gemeldet(self):
        # Sonst wäre eine frisch angelegte Suche stundenlang stumm — also genau
        # dann, wenn man sie beobachtet.
        self.assertTrue(pricing.is_deal(None, 30))


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.stats = pricing.stats_from(MARKT)

    def test_unter_median_wird_hervorgehoben(self):
        text = pricing.label(35.0, self.stats)
        self.assertIn("35 % unter**", text)
        self.assertIn("33 Vergleiche", text)

    def test_ueber_median(self):
        text = pricing.label(-20.0, self.stats)
        self.assertIn("über Median", text)
        self.assertNotIn("unter**", text)

    def test_auf_median_niveau(self):
        self.assertIn("Median-Niveau", pricing.label(0.4, self.stats))

    def test_ohne_urteil_kein_text(self):
        self.assertIsNone(pricing.label(None, self.stats))
        self.assertIsNone(pricing.label(30.0, None))


if __name__ == "__main__":
    unittest.main()
