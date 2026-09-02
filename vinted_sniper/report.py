"""Der Startbericht: was der Sniper gerade tut — ohne dass jemand nachsieht.

Ob abgefragt wird, wie entdoppelt wird, ob Kaufprofile greifen, wer bei einem
Ausfall angepingt wird: das steht alles in der `.env`, und niemand liest die
`.env`. Deshalb sagt es der Sniper bei jedem Start selbst, in Discord und im
Panel. Was nicht so ist, wie man es erwarten würde, ist markiert.

Reine Funktionen ohne Discord und ohne Datenbank, damit sich das ohne beides
prüfen lässt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .db import Watch
from .vinted import domains


@dataclass(frozen=True)
class Row:
    label: str
    value: str
    # Etwas, das man wissen sollte — nicht zwingend ein Fehler.
    warn: bool = False
    # Ein Zustand, in dem der Sniper nicht sucht — der wird wie ein Ausfall
    # behandelt, mit Erwähnung.
    alarm: bool = False


_SCOPES = {
    "all": "ein Artikel, ein Alert — über alle Suchen und Länder",
    "group": "einmal je Suche; verschiedene Suchen dürfen denselben Artikel melden",
    "watch": "AUS — jede Suche und jedes Land meldet für sich",
}


def _laender(watches: Sequence[Watch]) -> str:
    """„🇩🇪 de, 🇫🇷 fr, 🇬🇧 co.uk" — jedes Land einmal, in Reihenfolge der Suchen."""
    eintraege: list[str] = []
    for watch in watches:
        domain = domains.lookup(watch.host)
        kuerzel = domain.host.split("vinted.", 1)[-1] if "vinted." in domain.host else domain.host
        eintrag = f"{domain.flag} {kuerzel}".strip()
        if eintrag not in eintraege:
            eintraege.append(eintrag)
    return ", ".join(eintraege) if eintraege else "—"


def rows(settings: Any, watches: Sequence[Watch], profiles: Sequence[Any]) -> list[Row]:
    """Die Zeilen des Berichts, in der Reihenfolge, in der sie wichtig sind."""
    aktiv = [w for w in watches if w.enabled]
    ergebnis: list[Row] = []

    if getattr(settings, "polling_enabled", True):
        takt = sum(w.interval for w in aktiv) / len(aktiv) if aktiv else 0.0
        gruppen = len({w.group_key for w in aktiv})
        ergebnis.append(
            Row(
                "Abfrage",
                f"läuft — {len(aktiv)} Suche(n) in {gruppen} Gruppe(n), "
                f"im Schnitt alle {takt:.0f} s"
                if aktiv
                else "läuft, aber es ist keine Suche angelegt",
                warn=not aktiv,
            )
        )
    else:
        ergebnis.append(
            Row(
                "Abfrage",
                "AUS (POLLING=off) — es wird nichts gesucht. Treffer kommen nur "
                "aus Vinteds eigenen Benachrichtigungen und werden mit `/pruefen` "
                "gerechnet.",
                warn=True,
                alarm=True,
            )
        )

    ergebnis.append(Row("Länder", _laender(aktiv)))

    scope = getattr(settings, "dedupe_scope", "all")
    ergebnis.append(
        Row("Entdopplung", _SCOPES.get(scope, scope), warn=(scope == "watch"))
    )

    if profiles:
        namen = ", ".join(getattr(p, "name", str(p)) for p in profiles)
        ergebnis.append(Row("Kaufprofile", f"{len(profiles)} — {namen}"))
    else:
        ergebnis.append(
            Row(
                "Kaufprofile",
                "keine — jeder neue Treffer wird gemeldet, ohne Rechnung und Ampel "
                "(profiles.toml fehlt)",
                warn=True,
            )
        )

    erwaehnung = getattr(settings, "alert_mention", "") or ""
    ergebnis.append(
        Row(
            "Ausfall-Ping",
            erwaehnung if erwaehnung else "niemand wird getaggt (ALERT_MENTION leer)",
            warn=not erwaehnung,
        )
    )

    stunden = getattr(settings, "alert_retention_hours", 0) or 0
    ergebnis.append(
        Row("Aufräumen", f"Alerts älter als {stunden} h werden gelöscht" if stunden else "aus")
    )

    proxys = len(getattr(settings, "proxies", []) or [])
    ergebnis.append(Row("Proxy", f"{proxys} Sitzung(en)" if proxys else "keiner — eigene IP"))

    heartbeat = getattr(settings, "heartbeat_url", "") or ""
    ergebnis.append(
        Row(
            "Totmannschalter",
            "an" if heartbeat else "aus — ein toter Server fällt nicht auf (HEARTBEAT_URL leer)",
            warn=not heartbeat,
        )
    )
    return ergebnis


def is_alarm(zeilen: Sequence[Row]) -> bool:
    """Ob der Bericht wie ein Ausfall behandelt wird — mit Erwähnung.

    Nur die Abfrage zählt: ohne sie sucht der Sniper nicht, und das ist genau
    der Zustand, den man getaggt bekommen will. Fehlende Profile oder ein
    fehlender Totmannschalter sind Hinweise, kein Alarm.
    """
    return any(z.alarm for z in zeilen)


def title(zeilen: Sequence[Row]) -> str:
    return "Sniper gestartet — ABFRAGE AUS" if is_alarm(zeilen) else "Sniper gestartet"


def discord_text(zeilen: Sequence[Row]) -> str:
    return "\n".join(
        f"{'⚠️ ' if z.warn else ''}**{z.label}:** {z.value}" for z in zeilen
    )
