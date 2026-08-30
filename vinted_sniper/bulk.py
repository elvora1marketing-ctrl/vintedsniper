"""Mehrere Such-URLs auf einmal einlesen.

Eine URL je Zeile — genau das, was beim Kopieren aus dem Browser entsteht.
Optional lässt sich ein Name voranstellen: `Nike 44 | https://…`.

Das Einlesen ist bewusst von Datenbank und Netz getrennt: `parse_import` ist
eine reine Funktion und deshalb prüfbar, ohne dass irgendetwas läuft. Wer die
Ergebnisse übernimmt — Panel oder Slash-Command — entscheidet selbst, was er
mit den Fehlerzeilen macht.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .vinted.urls import InvalidSearchURL, SearchQuery, parse_search_url

# Obergrenze pro Vorgang. Nicht wegen der Technik, sondern weil jede Suche
# dauerhaft Abfragen an Vinted schickt: 200 Suchen im Minutentakt sind ein
# sicherer Weg, sich sperren zu lassen.
MAX_LINES = 200


@dataclass
class ImportEntry:
    """Eine Zeile, die sich als Vinted-Suche lesen ließ."""

    line: int
    name: str
    url: str
    query: SearchQuery


@dataclass
class ImportProblem:
    """Eine Zeile, die nicht verwendbar war — mit Grund."""

    line: int
    text: str
    reason: str

    def describe(self) -> str:
        gekuerzt = self.text if len(self.text) <= 60 else self.text[:57] + "…"
        return f"Zeile {self.line}: {gekuerzt} — {self.reason}"


@dataclass
class ImportPlan:
    entries: list[ImportEntry] = field(default_factory=list)
    problems: list[ImportProblem] = field(default_factory=list)
    # Zeilen, die dieselbe Suche wie eine frühere Zeile ergaben.
    duplicates: int = 0


def _split_name(line: str) -> tuple[str, str]:
    """`Name | URL` trennen, aber eine URL mit `|` darin nicht zerlegen."""
    if "|" not in line:
        return "", line
    kopf, _, rest = line.partition("|")
    kopf = kopf.strip()
    if not kopf or "://" in kopf or kopf.lower().startswith("www."):
        return "", line
    return kopf, rest.strip()


def _clean(raw: str) -> str:
    """Übliche Beifänge beim Kopieren entfernen.

    Aus Chat-Programmen und Markdown kommen Adressen gern in spitzen Klammern
    oder Anführungszeichen an; Aufzählungszeichen stammen aus Notizlisten.
    """
    line = raw.strip()
    for zeichen in ("- ", "* ", "• "):
        if line.startswith(zeichen):
            line = line[len(zeichen) :].strip()
            break
    return line.strip("<>\"'` \t")


def parse_import(text: str) -> ImportPlan:
    """Einen mehrzeiligen Text in Suchen und Fehlerzeilen zerlegen.

    Leerzeilen und Zeilen, die mit `#` beginnen, werden übersprungen.
    """
    plan = ImportPlan()
    gesehen: set[str] = set()

    for nummer, rohzeile in enumerate(text.splitlines(), start=1):
        zeile = _clean(rohzeile)
        if not zeile or zeile.startswith("#"):
            continue

        if len(plan.entries) >= MAX_LINES:
            plan.problems.append(
                ImportProblem(
                    nummer,
                    zeile,
                    f"übersprungen — mehr als {MAX_LINES} Suchen auf einmal",
                )
            )
            continue

        name, url = _split_name(zeile)
        try:
            query = parse_search_url(url)
        except InvalidSearchURL as exc:
            plan.problems.append(ImportProblem(nummer, zeile, str(exc)))
            continue

        kanonisch = query.web_url()
        if kanonisch in gesehen:
            plan.duplicates += 1
            continue
        gesehen.add(kanonisch)

        plan.entries.append(
            ImportEntry(
                line=nummer,
                name=(name or query.scalars.get("search_text") or query.host)[:80],
                url=kanonisch,
                query=query,
            )
        )

    return plan


def summarize(plan: ImportPlan, *, angelegt: int, bekannt: int = 0) -> str:
    """Ergebnis in einem Satz, den man ohne Nachdenken versteht."""
    teile = [f"{angelegt} Suche(n) angelegt"]
    if bekannt:
        teile.append(f"{bekannt} schon vorhanden")
    if plan.duplicates:
        teile.append(f"{plan.duplicates} doppelt in der Eingabe")
    if plan.problems:
        teile.append(f"{len(plan.problems)} Zeile(n) fehlerhaft")
    return ", ".join(teile) + "."
