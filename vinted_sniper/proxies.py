"""Proxy-Listen einlesen — aus der `.env` oder aus einer Datei.

Anbieter geben ihre Listen in unterschiedlichen Schreibweisen heraus. Webshare
etwa liefert `host:port:benutzer:passwort`, andere die URL-Form
`http://benutzer:passwort@host:port`. Beides wird hier akzeptiert und
vereinheitlicht, damit niemand tausende Zeilen von Hand umformatiert.

Große Listen gehören in eine Datei: eine Umgebungsvariable mit ein paar tausend
Einträgen ist weder les- noch wartbar.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)


def parse_proxy_line(line: str) -> str | None:
    """Eine Zeile einer Proxy-Liste in eine URL übersetzen.

    Erkennt:
      * `http://benutzer:passwort@host:port` (unverändert übernommen)
      * `host:port:benutzer:passwort` (Webshare & Co.)
      * `host:port` (ohne Zugangsdaten)

    Gibt `None` für Leerzeilen und Kommentare zurück.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if "://" in line:
        return line

    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, user, password = parts
        # Zugangsdaten können Sonderzeichen enthalten, die eine URL sonst
        # zerlegen würden.
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"

    log.warning("Proxy-Zeile nicht verstanden, übersprungen: %r", line[:60])
    return None


# Mehr als das ist kein Vorrat mehr, sondern ein Fehler in der Konfiguration —
# und würde beim Start unnötig Speicher und Logzeilen kosten.
MAX_SESSIONS = 20_000


def expand_template(template: str, count: int) -> list[str]:
    """Aus einer Vorlage mit `{n}` durchnummerierte Sitzungen erzeugen.

    Anbieter wie Webshare vergeben keine Proxy-Liste, sondern eine
    Sitzungskennung: `benutzer-DE-1`, `benutzer-DE-2`, … Jede Nummer bekommt
    eine eigene IP aus demselben Pool. Diese Zeilen unterscheiden sich also nur
    in einer Zahl — sie als Datei mit tausenden Zeilen zu pflegen, ist unnötig.

    `p.webshare.io:80:benutzer-DE-{n}:passwort` mit `count=4577` ergibt genau
    die Liste, die man sonst abtippt.
    """
    if not template or count <= 0:
        return []
    if "{n}" not in template:
        log.error(
            "PROXIES_TEMPLATE enthält kein {n} — ohne die Nummer wären alle "
            "Einträge identisch. Beispiel: host:port:benutzer-DE-{n}:passwort"
        )
        return []
    if count > MAX_SESSIONS:
        log.warning(
            "PROXIES_SESSIONS=%d ist unplausibel hoch, begrenze auf %d.",
            count,
            MAX_SESSIONS,
        )
        count = MAX_SESSIONS
    return [template.replace("{n}", str(nummer)) for nummer in range(1, count + 1)]


def load_proxies(
    *,
    inline: str,
    path: Path | None,
    template: str = "",
    sessions: int = 0,
) -> list[str]:
    """Proxys aus `PROXIES`, `PROXIES_FILE` und der Sitzungsvorlage zusammenführen.

    Doppelte Einträge fliegen raus, die Reihenfolge bleibt erhalten — der Bot
    arbeitet die Liste bei Blockaden der Reihe nach ab.
    """
    lines: list[str] = list(expand_template(template, sessions))

    for chunk in inline.replace("\n", ",").split(","):
        lines.append(chunk)

    if path is not None:
        if path.is_dir():
            # Dieselbe Docker-Falle wie bei searches.toml: fehlt die Datei beim
            # Start, legt Docker an ihrer Stelle ein Verzeichnis an.
            log.warning(
                "%s ist ein Verzeichnis statt einer Datei — Proxy-Liste wird "
                "ignoriert. Datei anlegen und Container neu starten.",
                path,
            )
        elif path.exists():
            try:
                lines.extend(path.read_text(encoding="utf-8").splitlines())
            except OSError as exc:
                log.error("Proxy-Datei %s nicht lesbar: %s", path, exc)
        else:
            log.info("Keine Proxy-Datei unter %s — es wird ohne Proxy gearbeitet.", path)

    proxies: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        parsed = parse_proxy_line(raw)
        if parsed is not None and parsed not in seen:
            seen.add(parsed)
            proxies.append(parsed)

    if proxies:
        log.info("%d Proxy(s) geladen.", len(proxies))
    return proxies
