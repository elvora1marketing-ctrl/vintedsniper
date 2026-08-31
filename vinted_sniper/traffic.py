"""Wie viel Datenvolumen der Sniper durch den Proxy schiebt.

Bei Anbietern wie Webshare kostet nicht die Anzahl der Abfragen, sondern das
übertragene Volumen. Ist das Kontingent leer, liefern alle Proxys HTTP 402 und
der Sniper steht — ohne dass vorher irgendwas darauf hingedeutet hätte.

Geschätzte Zahlen helfen da wenig: wie groß eine Antwort wirklich ist, hängt an
`PER_PAGE`, an der Kategorie und daran, ob Vinted gerade komprimiert. Deshalb
wird gezählt statt geraten.

Gemessen wird der Rumpf der Antwort, so wie er ankommt. Header, TLS-Handshake
und die Anfragen selbst kommen beim Anbieter noch obendrauf — die echte
Abrechnung liegt also etwas über dem hier angezeigten Wert.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


def human(bytes_: float) -> str:
    """Bytes lesbar machen — mit Komma, wie im Deutschen üblich."""
    for einheit, faktor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if bytes_ >= faktor:
            return f"{bytes_ / faktor:.1f}".replace(".", ",") + f" {einheit}"
    return f"{int(bytes_)} B"


@dataclass
class Meter:
    """Zählt übertragene Bytes — insgesamt, pro Tag und pro Host."""

    total: int = 0
    requests: int = 0
    day: str = ""
    day_bytes: int = 0
    day_requests: int = 0
    per_host: dict[str, int] = field(default_factory=dict)

    def _heute(self) -> str:
        return dt.date.today().isoformat()

    def record(self, host: str, size: int) -> None:
        if size <= 0:
            return
        heute = self._heute()
        if self.day != heute:
            # Tageswechsel: der Vortag interessiert nicht mehr, das Kontingent
            # rechnet der Anbieter ohnehin selbst zusammen.
            self.day = heute
            self.day_bytes = 0
            self.day_requests = 0

        self.total += size
        self.requests += 1
        self.day_bytes += size
        self.day_requests += 1
        self.per_host[host] = self.per_host.get(host, 0) + size

    @property
    def average(self) -> float:
        """Durchschnittliche Antwortgröße — die Zahl, mit der man hochrechnet."""
        return self.day_bytes / self.day_requests if self.day_requests else 0.0

    def forecast(self, watches: int, interval: float) -> int:
        """Hochrechnung auf 30 Tage bei gegebener Anzahl Suchen und Takt.

        Genau das, was man vor dem Nachkaufen wissen will: reicht das
        Kontingent für einen Monat, oder ist es in drei Tagen weg?
        """
        if watches <= 0 or interval <= 0 or not self.average:
            return 0
        abfragen_pro_tag = watches * 86_400 / interval
        return int(abfragen_pro_tag * self.average * 30)

    def summary(self) -> str:
        """Einzeiler für Status und Panel."""
        if not self.requests:
            return "noch nichts gemessen"
        return (
            f"heute {human(self.day_bytes)} in {self.day_requests} Abfragen "
            f"(Ø {human(self.average)}) · insgesamt {human(self.total)}"
        )
