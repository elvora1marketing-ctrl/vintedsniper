"""Preisreferenz: was ist an einem Fund wirklich ein Schnäppchen?

Ein Sniper, der jeden neuen Treffer meldet, produziert vor allem Rauschen. Zum
Weiterverkaufen zählt nicht „neu", sondern „deutlich unter dem, was
Vergleichbares kostet".

Die Vergleichsbasis entsteht nebenbei: der Sniper sieht bei jedem Durchlauf
ohnehin die aktuellen Angebote einer Suche. Ihre Preise werden gesammelt, und
ein neuer Fund wird gegen den Median dieser Sammlung gehalten.

**Was das ist und was nicht:** Der Median ist der *Angebotspreis* vergleichbarer
Artikel, nicht der erzielte Verkaufspreis. Vinted gibt Verkaufspreise nicht
heraus. „30 % unter Median" heißt also „deutlich günstiger als das, was gerade
sonst angeboten wird" — ein brauchbarer Näherungswert, keine Gewinngarantie.
Wie gut er ist, hängt daran, wie eng die Suche gefasst ist: „Nike" mischt
Socken mit Sneakern, „Nike Air Max 90 Gr. 44" nicht.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# Unter so vielen Vergleichswerten ist der Median Zufall. Bis dahin wird nur
# gesammelt und alles gemeldet — lieber Rauschen als falsche Sicherheit.
MIN_SAMPLES = 25


@dataclass(frozen=True)
class PriceStats:
    """Verteilung der Angebotspreise einer Suche."""

    median: float
    count: int
    cheapest: float

    @property
    def reliable(self) -> bool:
        return self.count >= MIN_SAMPLES and self.median > 0


def stats_from(prices: list[float]) -> PriceStats | None:
    brauchbar = [p for p in prices if p > 0]
    if not brauchbar:
        return None
    return PriceStats(
        median=statistics.median(brauchbar),
        count=len(brauchbar),
        cheapest=min(brauchbar),
    )


def discount(price: float | None, stats: PriceStats | None) -> float | None:
    """Wie weit ein Preis unter dem Median liegt, in Prozent.

    `None`, solange die Datenlage nichts hergibt — dann wird gemeldet statt
    gefiltert. Ein Sniper, der wegen fehlender Vergleichswerte schweigt, ist
    schlimmer als einer, der zu viel meldet.
    """
    if price is None or price <= 0 or stats is None or not stats.reliable:
        return None
    return (stats.median - price) / stats.median * 100.0


def label(rabatt: float | None, stats: PriceStats | None) -> str | None:
    """Einzeiler fürs Embed — oder `None`, wenn es nichts zu sagen gibt."""
    if rabatt is None or stats is None:
        return None
    median = f"{stats.median:.0f}".replace(".", ",")
    if rabatt >= 1:
        return f"**{rabatt:.0f} % unter** Median ({median} €, {stats.count} Vergleiche)"
    if rabatt <= -1:
        return f"{abs(rabatt):.0f} % über Median ({median} €, {stats.count} Vergleiche)"
    return f"auf Median-Niveau ({median} €, {stats.count} Vergleiche)"


def is_deal(rabatt: float | None, schwelle: float) -> bool:
    """Soll gemeldet werden?

    Ohne Schwelle gilt alles als meldenswert. Ohne Datenlage ebenfalls — sonst
    wäre der Sniper in den ersten Stunden nach dem Anlegen einer Suche stumm,
    also genau dann, wenn man ihn beobachtet.
    """
    if schwelle <= 0 or rabatt is None:
        return True
    return rabatt >= schwelle
