"""Tests für den Startbericht — was der Sniper über sich selbst sagt."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()

from vinted_sniper import report  # noqa: E402
from vinted_sniper.db import Watch  # noqa: E402


def watch(nummer, host="www.vinted.de", *, enabled=True, interval=60, group="g"):
    return Watch(
        id=nummer, guild_id=1, channel_id=2, creator_id=3, name=f"w{nummer}",
        host=host, source_url="", query_json="{}", interval=interval,
        enabled=enabled, created_at=0, last_checked_at=None, last_error=None,
        hits=0, group_key=group,
    )


def einstellungen(**aenderungen):
    basis = dict(
        polling_enabled=True, dedupe_scope="all", alert_mention="<@1>",
        alert_retention_hours=24, proxies=["a", "b"], heartbeat_url="https://hc/x",
    )
    basis.update(aenderungen)
    return SimpleNamespace(**basis)


PROFILE = [SimpleNamespace(name="RL Quarter/Half Zip"), SimpleNamespace(name="RL Cable Knit")]


class ReportTests(unittest.TestCase):
    def zeile(self, zeilen, label):
        return next(z for z in zeilen if z.label == label)

    def test_alles_in_ordnung(self):
        zeilen = report.rows(
            einstellungen(), [watch(1), watch(2, "www.vinted.fr"), watch(3, enabled=False)],
            PROFILE,
        )
        self.assertFalse(any(z.warn for z in zeilen))
        self.assertFalse(report.is_alarm(zeilen))
        self.assertEqual(report.title(zeilen), "Sniper gestartet")
        self.assertIn("2 Suche(n)", self.zeile(zeilen, "Abfrage").value)
        self.assertIn("🇩🇪 de", self.zeile(zeilen, "Länder").value)
        self.assertIn("🇫🇷 fr", self.zeile(zeilen, "Länder").value)
        self.assertIn("RL Cable Knit", self.zeile(zeilen, "Kaufprofile").value)
        self.assertIn("24 h", self.zeile(zeilen, "Aufräumen").value)
        self.assertIn("2 Sitzung", self.zeile(zeilen, "Proxy").value)

    def test_abfrage_aus_ist_alarm(self):
        zeilen = report.rows(einstellungen(polling_enabled=False), [watch(1)], PROFILE)
        abfrage = self.zeile(zeilen, "Abfrage")
        self.assertTrue(abfrage.warn)
        self.assertIn("POLLING=off", abfrage.value)
        self.assertTrue(report.is_alarm(zeilen))
        self.assertIn("ABFRAGE AUS", report.title(zeilen))

    def test_hinweise_sind_kein_alarm(self):
        # Fehlende Profile, kein Ping-Ziel, kein Totmannschalter: markiert,
        # aber niemand wird deswegen um drei Uhr nachts getaggt.
        zeilen = report.rows(
            einstellungen(alert_mention="", heartbeat_url=""), [watch(1)], []
        )
        self.assertTrue(self.zeile(zeilen, "Kaufprofile").warn)
        self.assertTrue(self.zeile(zeilen, "Ausfall-Ping").warn)
        self.assertTrue(self.zeile(zeilen, "Totmannschalter").warn)
        self.assertFalse(report.is_alarm(zeilen))

    def test_entdopplung_aus_wird_markiert(self):
        zeilen = report.rows(einstellungen(dedupe_scope="watch"), [watch(1)], PROFILE)
        self.assertTrue(self.zeile(zeilen, "Entdopplung").warn)
        zeilen = report.rows(einstellungen(dedupe_scope="all"), [watch(1)], PROFILE)
        self.assertFalse(self.zeile(zeilen, "Entdopplung").warn)
        self.assertIn("ein Artikel, ein Alert", self.zeile(zeilen, "Entdopplung").value)

    def test_keine_suche_wird_markiert(self):
        zeilen = report.rows(einstellungen(), [], PROFILE)
        self.assertTrue(self.zeile(zeilen, "Abfrage").warn)
        self.assertFalse(report.is_alarm(zeilen), "kein Ausfall, nur leer")

    def test_discord_text_markiert_warnungen(self):
        zeilen = report.rows(einstellungen(polling_enabled=False), [watch(1)], PROFILE)
        text = report.discord_text(zeilen)
        self.assertIn("⚠️ **Abfrage:**", text)
        self.assertIn("**Länder:** 🇩🇪 de", text)



class WindowAndTrafficRowTests(unittest.TestCase):
    def zeile(self, zeilen, label):
        return next(z for z in zeilen if z.label == label)

    def test_rund_um_die_uhr_ohne_fenster(self):
        zeilen = report.rows(einstellungen(), [watch(1)], PROFILE)
        self.assertEqual(self.zeile(zeilen, "Aktiv").value, "rund um die Uhr")

    def test_fenster_wird_genannt(self):
        from vinted_sniper import schedule
        fenster = schedule.parse("08:00-23:00")
        zeilen = report.rows(einstellungen(active_hours=fenster), [watch(1)], PROFILE)
        self.assertIn("08:00–23:00", self.zeile(zeilen, "Aktiv").value)

    def test_volumen_ohne_messung(self):
        zeilen = report.rows(einstellungen(), [watch(1)], PROFILE)
        self.assertIn("noch nichts gemessen", self.zeile(zeilen, "Volumen").value)

    def test_volumen_mit_hochrechnung(self):
        from vinted_sniper import schedule
        from vinted_sniper.traffic import Meter
        meter = Meter()
        meter.record("www.vinted.de", 50_000)
        fenster = schedule.parse("08:00-20:00")  # 12 h = halber Tag
        zeilen = report.rows(
            einstellungen(active_hours=fenster), [watch(1, interval=60)], PROFILE,
            meter=meter,
        )
        text = self.zeile(zeilen, "Volumen").value
        self.assertIn("hochgerechnet", text)
        self.assertIn("12 h am Tag", text)
        # 1 Suche, alle 60 s, halber Tag: 720 Abfragen × 50 KB × 30 Tage ≈ 1,0 GB
        self.assertIn("1,0 GB", text)


if __name__ == "__main__":
    unittest.main()
