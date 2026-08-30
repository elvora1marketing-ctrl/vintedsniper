"""Zentrale Konfiguration, gelesen aus Environment-Variablen (.env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from .proxies import load_proxies

load_dotenv()

log = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r ist keine Zahl, nutze Default %s", name, raw, default)
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r ist keine Zahl, nutze Default %s", name, raw, default)
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


class Mode(str, Enum):
    """Wie der Sniper betrieben wird.

    `BOT` gibt Slash-Commands, braucht aber ein Bot-Token. `WEBHOOK` kommt ohne
    Token aus: Suchen stehen in einer TOML-Datei, Alerts gehen per Webhook raus.
    """

    BOT = "bot"
    WEBHOOK = "webhook"


@dataclass(frozen=True)
class Settings:
    # --- Discord ---
    # Leer = Webhook-Modus. Gesetzt = voller Bot mit Slash-Commands.
    discord_token: str
    # Optional: Slash-Commands sofort in dieser Guild registrieren (statt bis zu
    # 1h globaler Propagation). Für den Eigenbetrieb praktisch immer sinnvoll.
    guild_id: int | None
    # Ziel für Alerts im Webhook-Modus, und Fallback, wenn eine Suche in der
    # TOML-Datei kein eigenes Ziel nennt.
    alert_webhook_url: str
    # Suchdefinitionen für den Webhook-Modus.
    searches_path: Path

    # --- Web-Panel ---
    # Leer = Panel bleibt aus. Ohne Passwort könnte sonst jeder die Suchen
    # ändern, der die Adresse kennt.
    panel_password: str
    panel_host: str
    panel_port: int

    # --- Speicher ---
    db_path: Path

    # --- Polling ---
    default_interval: int
    min_interval: int
    per_page: int
    # Zufälliger Zuschlag auf jedes Intervall, damit die Requests kein
    # metronomisches Muster bilden.
    jitter: float
    # Items, die älter sind als das, werden beim ersten Lauf nie gealertet.
    max_item_age: int

    # --- Antibot / Netzwerk ---
    impersonate: str
    proxies: list[str] = field(default_factory=list)
    playwright_fallback: bool = True
    request_timeout: float = 20.0
    # Maximale Requests pro Minute und Domain über alle Watches hinweg.
    rate_limit_per_domain: int = 60

    log_level: str = "INFO"

    @property
    def mode(self) -> Mode:
        return Mode.BOT if self.discord_token else Mode.WEBHOOK

    @classmethod
    def load(cls, *, require_target: bool = True) -> "Settings":
        """Einstellungen einlesen.

        `require_target=False` überspringt die Prüfung auf ein Alert-Ziel —
        die Diagnose (`python -m vinted_sniper.check`) prüft nur die
        Netzwerkverbindung und braucht weder Bot noch Webhook.
        """
        token = os.getenv("DISCORD_TOKEN", "").strip()
        webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
        if require_target and not token and not webhook:
            raise SystemExit(
                "Es fehlt ein Ziel für die Alerts. Trage in der .env entweder "
                "ALERT_WEBHOOK_URL ein (schnellster Weg, kein Bot nötig) oder "
                "DISCORD_TOKEN für den vollen Bot mit Slash-Commands. "
                "Vorlage: .env.example"
            )

        guild_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
        guild_id = int(guild_raw) if guild_raw.isdigit() else None

        min_interval = max(5, _int("MIN_INTERVAL", 20))
        default_interval = max(min_interval, _int("DEFAULT_INTERVAL", 60))

        return cls(
            discord_token=token,
            guild_id=guild_id,
            alert_webhook_url=webhook,
            searches_path=Path(os.getenv("SEARCHES_PATH", "searches.toml")),
            panel_password=os.getenv("PANEL_PASSWORD", "").strip(),
            panel_host=os.getenv("PANEL_HOST", "0.0.0.0").strip() or "0.0.0.0",
            panel_port=_int("PANEL_PORT", 8080),
            db_path=Path(os.getenv("DB_PATH", "data/sniper.db")),
            default_interval=default_interval,
            min_interval=min_interval,
            per_page=max(1, min(96, _int("PER_PAGE", 20))),
            jitter=max(0.0, _float("JITTER", 0.25)),
            max_item_age=_int("MAX_ITEM_AGE", 900),
            impersonate=os.getenv("IMPERSONATE", "chrome124").strip() or "chrome124",
            proxies=load_proxies(
                inline=os.getenv("PROXIES", ""),
                path=Path(os.getenv("PROXIES_FILE", "proxies.txt")),
            ),
            playwright_fallback=_bool("PLAYWRIGHT_FALLBACK", True),
            request_timeout=_float("REQUEST_TIMEOUT", 20.0),
            rate_limit_per_domain=max(1, _int("RATE_LIMIT_PER_DOMAIN", 60)),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
