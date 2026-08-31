"""Der Wachhund: prüft regelmäßig, ob der Sniper noch tut, was er soll.

Läuft in beiden Betriebsarten und kennt nur zwei Wege nach draußen — eine
`send`-Funktion für Discord und eine URL für den Totmannschalter. Damit bleibt
er von Bot und Webhook unabhängig.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from . import health
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

# `send(titel, text, ist_alarm)` — die Zustellung kennt der Wachhund nicht.
Sender = Callable[[str, str, bool], Awaitable[None]]

# Kürzer als die Prüfung, damit ein Absturz feiner sichtbar wird: das
# Lebenszeichen entscheidet, wie groß eine gemeldete Lücke ausfällt.
HEARTBEAT_EVERY = 60.0


class Watchdog:
    def __init__(self, settings: Settings, db: Database, *, send: Sender) -> None:
        self.settings = settings
        self.db = db
        self._send = send
        self._task: asyncio.Task[None] | None = None
        # Damit nicht alle fünf Minuten dieselbe Erwähnung kommt.
        self._alarm_aktiv = False

    # ---------------------------------------------------------------- Start

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="watchdog")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ------------------------------------------------------------ Startlauf

    async def report_downtime(self) -> None:
        """Beim Start melden, wenn der Sniper eine Weile weg war.

        Der Prozess weiß nach einem Neustart nichts von seinem Vorleben — das
        Lebenszeichen in der Datenbank schon. Läuft der Container in einer
        Absturzschleife, entsteht so bei jedem Anlauf ein Eintrag.
        """
        letztes = await self.db.last_heartbeat()
        await self.db.touch_heartbeat()
        if letztes is None:
            return

        luecke = time.time() - letztes
        # Ein geordneter Neustart dauert Sekunden; erst darüber ist es ein
        # Ausfall, über den man Bescheid wissen will.
        if luecke < max(180.0, HEARTBEAT_EVERY * 3):
            return

        await self._send(
            "Sniper war offline",
            f"Der Sniper hat **{health.describe_gap(luecke)}** lang nicht gelaufen "
            "und ist gerade wieder gestartet. In der Zeit wurden keine Treffer "
            "gemeldet.\n\nWar das kein Neustart von dir, lohnt ein Blick in "
            "`docker compose logs --tail 100 sniper`.",
            True,
        )

    # -------------------------------------------------------------- Schleife

    async def _ping(self) -> None:
        """Totmannschalter anstupsen.

        Bewusst schweigsam: Fällt der fremde Dienst aus, ist das kein Grund,
        den Channel vollzuschreiben — der Sniper selbst läuft ja.
        """
        if not self.settings.heartbeat_url:
            return
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.settings.heartbeat_url) as antwort:
                    if antwort.status >= 400:
                        log.debug("Heartbeat-Ping: HTTP %s", antwort.status)
        except Exception as exc:
            log.debug("Heartbeat-Ping fehlgeschlagen: %s", exc)

    async def _check(self) -> None:
        watches = await self.db.list_watches()
        zustand = health.inspect(
            watches, stale_after=self.settings.health_stale_after
        )

        if health.should_alarm(zustand):
            if not self._alarm_aktiv:
                self._alarm_aktiv = True
                log.error(
                    "Wachhund: keine der %d aktiven Suchen liefert noch Ergebnisse.",
                    zustand.total,
                )
                await self._send(
                    "Sniper meldet nichts mehr", health.describe(zustand), True
                )
            return

        if self._alarm_aktiv:
            self._alarm_aktiv = False
            log.info("Wachhund: Suchen laufen wieder.")
            await self._send(
                "Sniper läuft wieder",
                f"**{zustand.total - zustand.failing}** von **{zustand.total}** "
                "Suchen liefern wieder Ergebnisse.",
                False,
            )

    async def _loop(self) -> None:
        naechste_pruefung = 0.0
        while True:
            try:
                await self.db.touch_heartbeat()
                await self._ping()
                if time.monotonic() >= naechste_pruefung:
                    naechste_pruefung = time.monotonic() + self.settings.health_every
                    await self._check()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Der Wachhund darf niemals den Sniper mitreißen — er ist die
                # Instanz, die im Fehlerfall noch reden können muss.
                log.exception("Wachhund-Durchlauf fehlgeschlagen.")
            await asyncio.sleep(HEARTBEAT_EVERY)
