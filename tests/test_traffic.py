"""Tests für die Verbrauchsmessung.

Beim Proxy zahlt man nach Volumen. Eine falsche Zahl hier führt dazu, dass das
Kontingent unbemerkt leerläuft — und dann steht der Sniper.
"""

import unittest

from vinted_sniper import traffic


class HumanTests(unittest.TestCase):
    def test_einheiten(self):
        self.assertEqual(traffic.human(512), "512 B")
        self.assertEqual(traffic.human(1024), "1,0 KB")
        self.assertEqual(traffic.human(1024 * 1024), "1,0 MB")
        self.assertEqual(traffic.human(1024**3 * 2.5), "2,5 GB")

    def test_null(self):
        self.assertEqual(traffic.human(0), "0 B")


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.meter = traffic.Meter()

    def test_zaehlt_zusammen(self):
        self.meter.record("www.vinted.de", 1000)
        self.meter.record("www.vinted.fr", 2000)
        self.assertEqual(self.meter.total, 3000)
        self.assertEqual(self.meter.requests, 2)
        self.assertEqual(self.meter.per_host["www.vinted.de"], 1000)

    def test_leere_antworten_zaehlen_nicht(self):
        # Sonst würde der Durchschnitt durch Fehlversuche nach unten verzerrt.
        self.meter.record("www.vinted.de", 0)
        self.meter.record("www.vinted.de", -5)
        self.assertEqual(self.meter.requests, 0)

    def test_durchschnitt(self):
        self.meter.record("h", 1000)
        self.meter.record("h", 3000)
        self.assertEqual(self.meter.average, 2000)

    def test_durchschnitt_ohne_daten(self):
        self.assertEqual(self.meter.average, 0.0)

    def test_tageswechsel_setzt_zurueck(self):
        self.meter.record("h", 5000)
        self.meter.day = "2020-01-01"  # so, als liefe der Bot seit gestern
        self.meter.record("h", 1000)
        self.assertEqual(self.meter.day_bytes, 1000)
        self.assertEqual(self.meter.day_requests, 1)
        # Der Gesamtwert läuft weiter.
        self.assertEqual(self.meter.total, 6000)

    def test_hochrechnung(self):
        # 30 KB je Abfrage, 35 Suchen alle 60s → 35 * 1440 Abfragen am Tag.
        self.meter.record("h", 30 * 1024)
        erwartet = 35 * 86_400 / 60 * 30 * 1024 * 30
        self.assertAlmostEqual(self.meter.forecast(35, 60), int(erwartet), delta=1000)

    def test_hochrechnung_ohne_datenlage(self):
        self.assertEqual(self.meter.forecast(35, 60), 0)

    def test_hochrechnung_ohne_suchen(self):
        self.meter.record("h", 1000)
        self.assertEqual(self.meter.forecast(0, 60), 0)
        self.assertEqual(self.meter.forecast(35, 0), 0)

    def test_zusammenfassung_vor_der_ersten_messung(self):
        self.assertEqual(self.meter.summary(), "noch nichts gemessen")

    def test_zusammenfassung(self):
        self.meter.record("h", 50_000)
        text = self.meter.summary()
        self.assertIn("heute", text)
        self.assertIn("1 Abfragen", text)
        self.assertIn("KB", text)


if __name__ == "__main__":
    unittest.main()
