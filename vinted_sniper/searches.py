"""Suchdefinitionen aus `searches.toml` — der Weg ohne Bot-Token.

Im Webhook-Modus gibt es keine Slash-Commands, also müssen die Suchen aus einer
Datei kommen. Die Datei ist die Wahrheit: was dort steht, läuft; was dort
verschwindet, wird beim nächsten Start abgeschaltet.

Bewusst `tomllib` statt YAML — das ist in Python 3.11 Standardbibliothek und
kostet keine zusätzliche Abhängigkeit.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .vinted.urls import InvalidSearchURL, SearchQuery, parse_search_url

if TYPE_CHECKING:
    from .db import Database, Watch

log = logging.getLogger(__name__)


class InvalidSearchFile(ValueError):
    """Die TOML-Datei ist unbrauchbar — mit einer Meldung, die weiterhilft."""


@dataclass(frozen=True)
class FileSearch:
    name: str
    query: SearchQuery
    interval: int
    webhook_url: str
    source_url: str


EXAMPLE = """\
# Beispiel — eine Suche pro [[search]]-Block:
[[search]]
name = "Nike Air Max"
url = "https://www.vinted.de/catalog?search_text=nike+air+max&price_to=60"
"""


def load_searches(
    path: Path,
    *,
    default_interval: int,
    min_interval: int,
    default_webhook: str,
) -> list[FileSearch]:
    """`searches.toml` einlesen und validieren.

    Wirft `InvalidSearchFile` mit einer konkreten Fehlerbeschreibung — im
    Webhook-Modus ist die Datei die einzige Eingabemöglichkeit, entsprechend
    genau muss die Fehlermeldung sein.
    """
    if not path.exists():
        raise InvalidSearchFile(
            f"{path} gibt es nicht. Lege die Datei an (Vorlage: "
            f"searches.example.toml).\n\n{EXAMPLE}"
        )
    if path.is_dir():
        # Klassische Docker-Falle: fehlt die Datei beim Start, legt Docker an
        # der Stelle des Bind-Mounts ein Verzeichnis an.
        raise InvalidSearchFile(
            f"{path} ist ein Verzeichnis, keine Datei. Das passiert, wenn Docker "
            "gestartet wurde, bevor die Datei existierte. Behebung:\n"
            f"  docker compose down && rm -rf {path} && "
            "cp searches.example.toml searches.toml && docker compose up -d"
        )

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidSearchFile(f"{path} ist kein gültiges TOML: {exc}") from exc

    raw_entries = data.get("search")
    if raw_entries is None:
        raise InvalidSearchFile(
            f"In {path} steht kein [[search]]-Block.\n\n{EXAMPLE}"
        )
    if not isinstance(raw_entries, list):
        raise InvalidSearchFile(
            f"`search` muss in {path} als [[search]] geschrieben werden "
            "(doppelte Klammern), sonst versteht TOML es nicht als Liste."
        )

    searches: list[FileSearch] = []
    seen_names: set[str] = set()

    for index, entry in enumerate(raw_entries, start=1):
        position = f"[[search]] Nr. {index}"
        if not isinstance(entry, dict):
            raise InvalidSearchFile(f"{position} ist kein Block mit Schlüsseln.")

        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise InvalidSearchFile(f"{position}: `url` fehlt.")

        try:
            query = parse_search_url(url)
        except InvalidSearchURL as exc:
            raise InvalidSearchFile(f"{position}: {exc}") from exc

        name = entry.get("name") or query.scalars.get("search_text") or query.host
        if not isinstance(name, str) or not name.strip():
            raise InvalidSearchFile(f"{position}: `name` ist leer.")
        name = name.strip()[:80]
        if name in seen_names:
            # Der Name ist der Schlüssel, über den eine Suche ihre Historie
            # wiederfindet — doppelt vergeben würde sie sich gegenseitig
            # überschreiben.
            raise InvalidSearchFile(
                f"{position}: den Namen „{name}“ gibt es schon. Namen müssen "
                "eindeutig sein."
            )
        seen_names.add(name)

        interval = entry.get("interval", default_interval)
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise InvalidSearchFile(f"{position}: `interval` muss eine Zahl sein.")
        if interval < min_interval:
            log.warning(
                "%s: interval=%ss liegt unter dem Minimum, nutze %ss.",
                position,
                interval,
                min_interval,
            )
            interval = min_interval

        webhook = entry.get("webhook", default_webhook)
        if not isinstance(webhook, str) or not webhook.strip():
            raise InvalidSearchFile(
                f"{position}: kein Alert-Ziel. Setze ALERT_WEBHOOK_URL in der "
                "`.env` oder `webhook = \"...\"` in diesem Block."
            )

        searches.append(
            FileSearch(
                name=name,
                query=query,
                interval=interval,
                webhook_url=webhook.strip(),
                source_url=query.web_url(),
            )
        )

    if not searches:
        raise InvalidSearchFile(f"In {path} steht keine einzige Suche.\n\n{EXAMPLE}")

    return searches


async def sync_to_db(db: "Database", searches: list[FileSearch]) -> list["Watch"]:
    """Datei-Suchen mit der Datenbank abgleichen.

    Bestehende Suchen behalten ihre ID und damit ihre Historie bereits
    gemeldeter Artikel — sonst würde jeder Neustart nach einer Dateiänderung
    einen Alert-Schwall auslösen. Verschwundene Suchen werden entfernt.
    """
    existing = {watch.name: watch for watch in await db.list_file_watches()}
    wanted = {search.name for search in searches}
    result: list["Watch"] = []

    for search in searches:
        current = existing.get(search.name)
        if current is None:
            watch = await db.add_watch(
                guild_id=0,
                channel_id=0,
                creator_id=0,
                name=search.name,
                query=search.query,
                source_url=search.source_url,
                interval=search.interval,
                webhook_url=search.webhook_url,
                origin="file",
            )
            log.info("Suche „%s“ neu übernommen (#%d).", watch.name, watch.id)
        else:
            await db.update_file_watch(
                current.id,
                query=search.query,
                source_url=search.source_url,
                interval=search.interval,
                webhook_url=search.webhook_url,
            )
            refreshed = await db.get_watch(current.id)
            assert refreshed is not None
            watch = refreshed
        result.append(watch)

    for name, watch in existing.items():
        if name not in wanted:
            await db.delete_watch(watch.id)
            log.info("Suche „%s“ (#%d) steht nicht mehr in der Datei — entfernt.", name, watch.id)

    return result
