"""Tests für das Zeitfenster."""

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from vinted_sniper import schedule
from vinted_sniper.traffic import Meter

BERLIN = ZoneInfo("Europe/Berlin")


def um(stunde, minute=0, tag=2):
    return dt.datetime(2026, 9, tag, stunde, minute, tzinfo=BERLIN)


class ParseTests(unittest.TestCase):
    def test_leer_ist_rund_um_die_uhr(self):
        self.assertIsNone(schedule.parse(""))
        self.assertIsNone(schedule.parse("   "))

    def test_schreibweisen(self):
        for text in ("08:00-23:00", "8-23", " 8:00 - 23:00 "):
            fenster = schedule.parse(text)
            self.assertEqual((fenster.start, fenster.end), (480, 1380), text)

    def test_24_uhr_ist_mitternacht(self):
        self.assertEqual(schedule.parse("06:00-24:00").end, 0)

    def test_kaputt(self):
        for text in ("acht bis elf", "8", "25:00-26:00", "10:00-10:00"):
            with self.assertRaises(schedule.InvalidWindow, msg=text):
                schedule.parse(text)

    def test_unbekannte_zeitzone(self):
        with self.assertRaises(schedule.InvalidWindow):
            schedule.parse("8-23", "Mars/Olympus")


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.tag = schedule.parse("08:00-23:00")
        self.nacht = schedule.parse("22:00-02:00")

    def test_offen_und_zu(self):
        self.assertTrue(self.tag.is_open(um(8)))
        self.assertTrue(self.tag.is_open(um(22, 59)))
        self.assertFalse(self.tag.is_open(um(23)))
        self.assertFalse(self.tag.is_open(um(7, 59)))

    def test_ueber_mitternacht(self):
        self.assertTrue(self.nacht.is_open(um(23)))
        self.assertTrue(self.nacht.is_open(um(1)))
        self.assertFalse(self.nacht.is_open(um(2)))
        self.assertFalse(self.nacht.is_open(um(12)))

    def test_zeitzone_wird_beachtet(self):
        # 07:00 UTC ist 09:00 in Berlin (Sommerzeit) — offen.
        utc = dt.datetime(2026, 9, 2, 7, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(self.tag.is_open(utc))

    def test_warten_bis_zum_morgen(self):
        self.assertEqual(self.tag.seconds_until_open(um(23)), 9 * 3600)
        self.assertEqual(self.tag.seconds_until_open(um(7, 30)), 1800)
        self.assertEqual(self.tag.seconds_until_open(um(12)), 0.0)

    def test_seit_wann_offen(self):
        self.assertEqual(self.tag.seconds_since_open(um(8, 5)), 300)
        self.assertEqual(self.tag.seconds_since_open(um(3)), 0.0)
        # Über Mitternacht: um 01:00 ist das Fenster seit 22:00 gestern offen.
        self.assertEqual(self.nacht.seconds_since_open(um(1)), 3 * 3600)

    def test_tagesanteil(self):
        self.assertAlmostEqual(self.tag.daily_fraction, 15 / 24)
        self.assertAlmostEqual(self.nacht.daily_fraction, 4 / 24)

    def test_beschreibung(self):
        self.assertEqual(self.tag.describe(), "08:00–23:00 (Europe/Berlin)")
        self.assertIn("gerade aktiv", self.tag.describe_now(um(12)))
        self.assertIn("Pause bis morgen 08:00", self.tag.describe_now(um(23, 30)))
        self.assertIn("Pause bis heute 08:00", self.tag.describe_now(um(6)))


class ForecastTests(unittest.TestCase):
    def test_hochrechnung_mit_fenster(self):
        meter = Meter()
        meter.record("www.vinted.de", 50_000)
        voll = meter.forecast(10, 60)
        halb = meter.forecast(10, 60, fraction=0.5)
        self.assertEqual(halb, voll // 2)
        self.assertEqual(meter.forecast(10, 60, fraction=2.0), voll)


if __name__ == "__main__":
    unittest.main()
