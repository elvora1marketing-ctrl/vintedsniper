"""Tests für die Ausfallerkennung.

Der teuerste Fehler hier ist ein Ausfall, der niemandem auffällt. Der
zweitteuerste ist eine Erwähnung, die so oft kommt, dass sie weggeklickt wird.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()

from vinted_sniper import health  # noqa: E402
from vinted_sniper.db import Watch  # noqa: E402

JETZT = 1_700_000_000.0


def watch(watch_id=1, *, enabled=True, error=None, checked=JETZT, interval=60):
    return Watch(
        id=watch_id,
        guild_id=0,
        channel_id=0,
        creator_id=0,
        name=f"Suche {watch_id}",
        host="www.vinted.de",
        source_url="https://www.vinted.de/catalog",
        query_json='{"host": "www.vinted.de", "lists": {}, "scalars": {}}',
        interval=interval,
        enabled=enabled,
        created_at=int(JETZT - 3600),
        last_checked_at=int(checked) if checked else None,
        last_error=error,
        hits=0,
    )


class InspectTests(unittest.TestCase):
    def pruefen(self, watches, stale_after=900.0):
        return health.inspect(watches, stale_after=stale_after, now=JETZT)

    def test_alles_gesund(self):
        zustand = self.pruefen([watch(1), watch(2)])
        self.assertEqual(zustand.total, 2)
        self.assertEqual(zustand.failing, 0)
        self.assertEqual(zustand.stale, 0)
        self.assertFalse(health.should_alarm(zustand))

    def test_pausierte_zaehlen_nicht_mit(self):
        # Eine pausierte Suche liefert absichtlich nichts — das ist kein Ausfall.
        zustand = self.pruefen([watch(1), watch(2, enabled=False, error="kaputt")])
        self.assertEqual(zustand.total, 1)
        self.assertFalse(health.should_alarm(zustand))

    def test_eine_von_zwei_hakt_ist_kein_alarm(self):
        # Dafür meldet der Monitor schon selbst. Eine Erwähnung ist für den
        # Fall reserviert, dass gar nichts mehr geht.
        zustand = self.pruefen([watch(1), watch(2, error="HTTP 403")])
        self.assertEqual(zustand.failing, 1)
        self.assertFalse(health.should_alarm(zustand))

    def test_alle_haken_ist_alarm(self):
        zustand = self.pruefen([watch(1, error="HTTP 402"), watch(2, error="HTTP 402")])
        self.assertTrue(zustand.all_failing)
        self.assertTrue(health.should_alarm(zustand))
        self.assertEqual(zustand.sample_error, "HTTP 402")

    def test_verstummte_suchen_sind_alarm(self):
        # Der wichtigere Fall: eine Suche, die gar nicht mehr durchläuft, setzt
        # auch keinen Fehler. Ohne diese Prüfung bliebe sie unbemerkt.
        stumm = watch(1, checked=JETZT - 3600)
        zustand = self.pruefen([stumm])
        self.assertEqual(zustand.failing, 0)
        self.assertEqual(zustand.stale, 1)
        self.assertTrue(health.should_alarm(zustand))

    def test_langsame_suchen_duerfen_laenger_schweigen(self):
        # Dreifaches Intervall als Frist: eine Suche im Stundentakt ist nach
        # 20 Minuten nicht „stumm".
        gemaechlich = watch(1, interval=1800, checked=JETZT - 1200)
        self.assertEqual(self.pruefen([gemaechlich]).stale, 0)

    def test_nie_geprueft_zaehlt_ab_anlage(self):
        # Sonst wäre jede frisch angelegte Suche sofort ein Alarm.
        neu = watch(1, checked=None)
        zustand = self.pruefen([neu], stale_after=7200.0)
        self.assertEqual(zustand.stale, 0)

    def test_ohne_suchen_kein_alarm(self):
        zustand = self.pruefen([])
        self.assertEqual(zustand.total, 0)
        self.assertFalse(health.should_alarm(zustand))

    def test_beschreibung_nennt_zahl_und_ursache(self):
        zustand = self.pruefen(
            [watch(1, error="HTTP 402 Payment Required", checked=JETZT - 1800)]
        )
        text = health.describe(zustand)
        self.assertIn("1", text)
        self.assertIn("402", text)
        self.assertIn("30 Minuten", text)


class GapTests(unittest.TestCase):
    def test_lesbare_dauer(self):
        self.assertEqual(health.describe_gap(300), "5 Minuten")
        self.assertEqual(health.describe_gap(3900), "1 Stunden 5 Minuten")
        self.assertEqual(health.describe_gap(90000), "1 Tage 1 Stunden")


if __name__ == "__main__":
    unittest.main()
