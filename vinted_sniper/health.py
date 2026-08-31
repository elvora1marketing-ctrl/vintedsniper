"""Überwachung: merken, wenn der Sniper nicht mehr tut, was er soll.

Ein Sniper, der still ausfällt, ist schlimmer als keiner: man verlässt sich auf
ihn und merkt tagelang nicht, dass nichts mehr kommt. Deshalb drei Stufen, die
unterschiedliche Ausfälle abdecken — keine einzelne deckt alle ab:

1. **Suchen hängen.** Der Prozess läuft, aber alle Abfragen scheitern (Proxy
   leer, IP gesperrt, Vinted ändert etwas). Das merkt der Bot selbst und meldet
   es in den Channel.
2. **Prozess war weg.** Container abgestürzt und neu gestartet, Server
   rebootet. Beim Start wird der letzte Lebenszeichen-Zeitstempel gelesen: liegt
   er zu weit zurück, gab es eine Lücke — und die wird gemeldet.
3. **Prozess ist weg und kommt nicht wieder.** Server aus, Docker tot,
   Internetleitung weg. Das kann der Bot grundsätzlich nicht melden — er läuft
   ja nicht. Dafür gibt es den Totmannschalter: ein regelmäßiger Ping an einen
   fremden Dienst, der Alarm schlägt, *wenn der Ping ausbleibt*.

Diese Datei enthält nur die Entscheidungslogik. Sie kennt weder Discord noch
die Datenbank und ist deshalb ohne beides prüfbar.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .db import Watch


@dataclass(frozen=True)
class Health:
    """Zustand aller aktiven Suchen zu einem Zeitpunkt."""

    total: int
    failing: int
    stale: int
    # Wie lange die am längsten unbeachtete Suche schon nichts mehr gemeldet hat.
    worst_silence: float
    # Beispielhafte Fehlermeldung — die Ursache ist meist bei allen dieselbe.
    sample_error: str | None

    @property
    def all_failing(self) -> bool:
        return self.total > 0 and self.failing == self.total

    @property
    def any_stale(self) -> bool:
        return self.stale > 0


def inspect(watches: list[Watch], *, stale_after: float, now: float | None = None) -> Health:
    """Den Zustand der aktiven Suchen feststellen.

    „Stale" heißt: die Suche wurde zu lange nicht erfolgreich geprüft. Das ist
    das verlässlichere Signal als die Fehlerzahl — eine Suche, die gar nicht
    mehr durchläuft, setzt auch keinen Fehler.
    """
    jetzt = time.time() if now is None else now
    aktiv = [w for w in watches if w.enabled]

    failing = [w for w in aktiv if w.last_error]
    stale: list[Watch] = []
    schlimmste = 0.0

    for watch in aktiv:
        # Nie geprüft zählt ab Anlagezeitpunkt — sonst wäre eine gerade
        # angelegte Suche sofort „stale".
        letzte = watch.last_checked_at or watch.created_at
        schweigen = max(0.0, jetzt - letzte)
        schlimmste = max(schlimmste, schweigen)
        # Großzügig: das Dreifache des eigenen Intervalls, mindestens aber die
        # konfigurierte Frist. Eine Suche im 10-Minuten-Takt darf länger
        # schweigen als eine im Minutentakt.
        frist = max(stale_after, watch.interval * 3)
        if schweigen > frist:
            stale.append(watch)

    return Health(
        total=len(aktiv),
        failing=len(failing),
        stale=len(stale),
        worst_silence=schlimmste,
        sample_error=failing[0].last_error if failing else None,
    )


def should_alarm(health: Health) -> bool:
    """Ist der Zustand meldenswert?

    Nur wenn **alle** Suchen betroffen sind. Eine einzelne hakende Suche meldet
    der Monitor bereits selbst; ein Alarm mit Erwähnung ist für den Fall da,
    dass gar nichts mehr geht — sonst stumpft die Erwähnung ab und wird
    weggeklickt.
    """
    if health.total == 0:
        return False
    return health.all_failing or health.stale == health.total


def describe(health: Health) -> str:
    """Was los ist, in einem Satz — und was es meist bedeutet."""
    minuten = int(health.worst_silence // 60)
    teile = [
        f"Keine der **{health.total}** aktiven Suchen liefert noch Ergebnisse."
    ]
    if minuten:
        teile.append(f"Seit **{minuten} Minuten** kam nichts mehr durch.")
    if health.sample_error:
        teile.append(f"Fehler: `{health.sample_error[:180]}`")
    teile.append(
        "Häufigste Ursachen: Proxy-Kontingent aufgebraucht (HTTP 402), "
        "Server-IP gesperrt (403) oder Vinted hat etwas geändert. "
        "Nachsehen mit `docker compose logs --tail 50 sniper`."
    )
    return "\n".join(teile)


def describe_gap(seconds: float) -> str:
    """Wie lange der Sniper weg war, in lesbar."""
    minuten = int(seconds // 60)
    if minuten < 60:
        return f"{minuten} Minuten"
    stunden = minuten // 60
    if stunden < 24:
        return f"{stunden} Stunden {minuten % 60} Minuten"
    return f"{stunden // 24} Tage {stunden % 24} Stunden"
