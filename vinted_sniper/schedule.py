"""Zeitfenster: wann der Sniper abfragt und wann er Pause macht.

Rund um die Uhr zu suchen kostet Proxy-Volumen für Stunden, in denen niemand
kauft. Nachts um drei wird auf Vinted wenig eingestellt, und wer dann einen
Alert bekommt, schläft ohnehin. `ACTIVE_HOURS=08:00-23:00` beschränkt das
Abfragen auf die Stunden, in denen es sich lohnt — und spart im Beispiel gut
ein Drittel des Volumens.

Das Fenster darf über Mitternacht gehen (`22:00-02:00`). Die Zeitzone kommt
aus `TIMEZONE`, damit „8 Uhr" auch dann 8 Uhr in Berlin heißt, wenn der
Container in UTC läuft.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FENSTER = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$")


class InvalidWindow(ValueError):
    """Das Zeitfenster ist nicht lesbar — mit einer Meldung, die weiterhilft."""


@dataclass(frozen=True)
class Window:
    # Minuten seit Mitternacht.
    start: int
    end: int
    timezone: str = "Europe/Berlin"

    @property
    def overnight(self) -> bool:
        return self.end <= self.start

    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> dt.datetime:
        return dt.datetime.now(self._tz())

    def _minute(self, at: dt.datetime) -> int:
        lokal = at.astimezone(self._tz())
        return lokal.hour * 60 + lokal.minute

    def is_open(self, at: dt.datetime | None = None) -> bool:
        minute = self._minute(at or self.now())
        if self.overnight:
            return minute >= self.start or minute < self.end
        return self.start <= minute < self.end

    def next_open(self, at: dt.datetime | None = None) -> dt.datetime:
        """Der nächste Zeitpunkt, an dem das Fenster aufgeht."""
        jetzt = (at or self.now()).astimezone(self._tz())
        beginn = jetzt.replace(
            hour=self.start // 60, minute=self.start % 60, second=0, microsecond=0
        )
        if beginn <= jetzt:
            beginn += dt.timedelta(days=1)
        return beginn

    def seconds_until_open(self, at: dt.datetime | None = None) -> float:
        jetzt = at or self.now()
        if self.is_open(jetzt):
            return 0.0
        return max(0.0, (self.next_open(jetzt) - jetzt).total_seconds())

    def seconds_since_open(self, at: dt.datetime | None = None) -> float:
        """Wie lange das Fenster schon offen ist — 0, wenn es zu ist.

        Braucht der Wachhund: direkt nach dem Aufgehen hat noch keine Suche
        gelaufen, und das ist kein Ausfall.
        """
        jetzt = (at or self.now()).astimezone(self._tz())
        if not self.is_open(jetzt):
            return 0.0
        beginn = jetzt.replace(
            hour=self.start // 60, minute=self.start % 60, second=0, microsecond=0
        )
        if beginn > jetzt:
            beginn -= dt.timedelta(days=1)
        return (jetzt - beginn).total_seconds()

    @property
    def daily_fraction(self) -> float:
        """Anteil des Tages, an dem gesucht wird — für die Volumen-Hochrechnung."""
        minuten = (self.end - self.start) % 1440 or 1440
        return minuten / 1440.0

    def describe(self) -> str:
        return f"{_hhmm(self.start)}–{_hhmm(self.end)} ({self.timezone})"

    def describe_now(self, at: dt.datetime | None = None) -> str:
        jetzt = at or self.now()
        if self.is_open(jetzt):
            return f"{self.describe()}, gerade aktiv"
        naechste = self.next_open(jetzt)
        heute = naechste.date() == jetzt.astimezone(self._tz()).date()
        return (
            f"{self.describe()}, gerade Pause bis "
            f"{'heute' if heute else 'morgen'} {naechste.strftime('%H:%M')}"
        )


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse(text: str, timezone: str = "Europe/Berlin") -> Window | None:
    """`08:00-23:00`, `8-23`, `22:00-02:00`. Leer = kein Fenster, rund um die Uhr."""
    if not text or not text.strip():
        return None
    treffer = _FENSTER.match(text)
    if not treffer:
        raise InvalidWindow(
            f"ACTIVE_HOURS={text!r} ist kein Zeitfenster. Beispiel: 08:00-23:00"
        )
    h1, m1, h2, m2 = treffer.groups()
    start = int(h1) * 60 + int(m1 or 0)
    end = int(h2) * 60 + int(m2 or 0)
    if end == 1440:
        end = 0
    if not (0 <= start < 1440 and 0 <= end < 1440):
        raise InvalidWindow(f"ACTIVE_HOURS={text!r}: Stunden 0–24, Minuten 0–59.")
    if start == end:
        raise InvalidWindow(
            f"ACTIVE_HOURS={text!r}: Anfang und Ende sind gleich. Für rund um "
            "die Uhr die Variable leer lassen."
        )
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidWindow(
            f"TIMEZONE={timezone!r} ist keine bekannte Zeitzone. Beispiel: Europe/Berlin"
        ) from exc
    return Window(start, end, timezone)
