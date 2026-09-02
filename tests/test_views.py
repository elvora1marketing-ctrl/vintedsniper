"""Rendert die Panel-Übersicht einmal komplett.

Die Übersicht ist ein großer f-String. Ein vergessener Name darin fällt beim
Import nicht auf, sondern erst, wenn die erste Suche angezeigt werden soll —
und dann liefert das Panel nur noch einen Fehler 500.
"""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()

from vinted_sniper import report  # noqa: E402
from vinted_sniper.db import Watch  # noqa: E402
from vinted_sniper.panel import views  # noqa: E402


def watch(nummer, *, dupes=0, error=None, enabled=True):
    query = {"host": "www.vinted.de", "lists": {}, "scalars": {"search_text": "rl zip"}}
    return Watch(
        id=nummer, guild_id=1, channel_id=2, creator_id=3, name=f"Suche {nummer}",
        host="www.vinted.de", source_url="https://www.vinted.de/catalog?search_text=rl",
        query_json=json.dumps(query), interval=60, enabled=enabled, created_at=0,
        last_checked_at=None, last_error=error, hits=3, group_key="g", dupes=dupes,
    )


class DashboardRenderTests(unittest.TestCase):
    def render(self, watches, **extra):
        return views.dashboard(
            watches=watches, running={w.id for w in watches},
            sessions={"www.vinted.de": "ok"},
            started_at=dt.datetime.now(dt.timezone.utc), **extra,
        )

    def test_leer(self):
        html = self.render([])
        self.assertIn("Noch keine Suche", html)

    def test_mit_suchen(self):
        html = self.render([watch(1), watch(2, dupes=47), watch(3, error="403", enabled=False)])
        self.assertIn("Suche 1", html)
        self.assertIn("47 doppelt", html)
        self.assertIn("pausiert", html)
        self.assertIn("403", html)

    def test_ohne_dubletten_kein_hinweis(self):
        self.assertNotIn(" doppelt</span>", self.render([watch(1)]))

    def test_betriebsbericht(self):
        einstellungen = SimpleNamespace(
            polling_enabled=False, dedupe_scope="all", alert_mention="",
            alert_retention_hours=24, proxies=[], heartbeat_url="",
        )
        zeilen = report.rows(einstellungen, [watch(1)], [])
        html = self.render([watch(1)], betrieb=zeilen)
        self.assertIn("Betrieb", html)
        self.assertIn("POLLING=off", html)
        self.assertIn("class=warn", html)

    def test_ohne_bericht_kein_kasten(self):
        self.assertNotIn(">Betrieb<", self.render([watch(1)]))


if __name__ == "__main__":
    unittest.main()
