"""Zentrale Konfiguration, gelesen aus Environment-Variablen (.env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from . import schedule
from .proxies import load_proxies
from .schedule import Window
from .vinted import domains

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


def _ids(name: str) -> tuple[int, ...]:
    """Komma- oder leerzeichengetrennte Discord-IDs einlesen."""
    raw = os.getenv(name, "")
    werte: list[int] = []
    for teil in raw.replace(",", " ").split():
        if teil.isdigit():
            werte.append(int(teil))
        else:
            log.warning("%s: %r ist keine Discord-ID, wird übersprungen.", name, teil)
    return tuple(werte)


def _words(name: str) -> tuple[str, ...]:
    """Komma-getrennte Stichwörter, kleingeschrieben und entdoppelt."""
    roh = os.getenv(name, "")
    werte = [teil.strip().lower() for teil in roh.split(",")]
    return tuple(dict.fromkeys(wort for wort in werte if wort))


def _mention(name: str) -> str:
    """Erwähnung für Ausfallmeldungen aufbereiten.

    Discord pingt nur über die Benutzer-ID, nicht über den Namen. Reine Zahlen
    werden deshalb zu `<@id>` gemacht; alles andere (`@here`, eine fertige
    Rollen-Erwähnung) bleibt, wie es ist. Mehrere durch Komma trennen.
    """
    roh = os.getenv(name, "").strip()
    if not roh:
        return ""
    teile: list[str] = []
    for eintrag in roh.replace(",", " ").split():
        if eintrag.isdigit():
            teile.append(f"<@{eintrag}>")
        elif eintrag.startswith("@") and eintrag[1:].isdigit():
            teile.append(f"<@{eintrag[1:]}>")
        else:
            if eintrag.startswith("@") and eintrag not in ("@here", "@everyone"):
                log.warning(
                    "%s=%r: Discord pingt nicht über den Namen, sondern über die "
                    "Benutzer-ID. Entwicklermodus einschalten, Rechtsklick auf "
                    "den Namen → „ID kopieren“, und die Zahl hier eintragen.",
                    name,
                    eintrag,
                )
            teile.append(eintrag)
    return " ".join(teile)


def _choice(name: str, default: str, erlaubt: tuple[str, ...]) -> str:
    roh = os.getenv(name, "").strip().lower()
    if not roh:
        return default
    if roh not in erlaubt:
        log.warning(
            "%s=%r ist unbekannt (erlaubt: %s), nutze %s.",
            name,
            roh,
            ", ".join(erlaubt),
            default,
        )
        return default
    return roh


def _countries(name: str) -> tuple[str, ...]:
    """Länderliste wie `fr,nl,it` zu Vinted-Hosts auflösen."""
    gefunden, unbekannt = domains.parse_list(os.getenv(name, ""))
    for eintrag in unbekannt:
        log.warning(
            "%s: „%s“ ist kein bekanntes Vinted-Land und wird übersprungen. "
            "Erlaubt sind Kürzel wie fr, nl, it, es oder uk.",
            name,
            eintrag,
        )
    return tuple(d.host for d in gefunden)


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
    # Kaufprofile: was ein Fund einbringen muss, um gemeldet zu werden.
    profiles_path: Path

    # --- Web-Panel ---
    # Leer = Panel bleibt aus. Ohne Passwort könnte sonst jeder die Suchen
    # ändern, der die Adresse kennt.
    panel_password: str
    panel_host: str
    panel_port: int

    # --- Länder ---
    # Jede Suche läuft zusätzlich automatisch auf diesen Domains. Derselbe
    # Artikel kostet im Ausland oft weniger — wer nur eine Domain beobachtet,
    # sieht ihn nie. Leer = nur die Domain aus der jeweiligen Such-URL.
    extra_countries: tuple[str, ...]
    # Wie weit ein bereits gemeldeter Artikel andere Suchen stummschaltet.
    # `all` = jede Suche (Standard: ein Artikel, ein Alert), `group` = nur
    # die Länderkopien derselben Suche, `watch` = gar nicht, jede Suche
    # meldet für sich. Neu eingestellte Kopien desselben Artikels werden in
    # jedem Modus zusammengefasst.
    dedupe_scope: str

    # --- Überwachung ---
    # Wen der Bot bei einem Ausfall anpingt. Roh übernommen, damit auch
    # `@here` oder eine Rolle möglich ist.
    alert_mention: str
    # Channel für Ausfallmeldungen. Leer = der Channel der ersten Suche bzw.
    # das Webhook-Ziel.
    health_channel_id: int
    # Ab wann eine Suche als „meldet nichts mehr" gilt (Sekunden).
    health_stale_after: float
    # Abstand zwischen zwei Prüfungen (Sekunden).
    health_every: float
    # Totmannschalter: URL, die regelmäßig angepingt wird. Bleibt der Ping aus,
    # schlägt der fremde Dienst Alarm — das ist der einzige Weg, einen toten
    # Server zu bemerken. Leer = aus.
    heartbeat_url: str

    # --- Aufräumen ---
    # Alerts älter als das werden automatisch gelöscht. 0 = aus.
    alert_retention_hours: int
    # Channels, in denen aufgeräumt wird. Leer = alle Channels, in die der Bot
    # selbst alertet. Für den Webhook-Modus muss die ID hier stehen: aus einer
    # Webhook-URL lässt sich der Channel nicht ableiten.
    cleanup_channel_ids: tuple[int, ...]

    # --- Speicher ---
    db_path: Path

    # --- Polling ---
    # `False` = der Sniper fragt Vinted von sich aus nicht mehr ab. Panel,
    # Bewertung, Entdopplung und Discord laufen weiter; Treffer kommen dann
    # aus Vinteds eigenen Benachrichtigungen und werden hier nur bewertet.
    polling_enabled: bool
    # Zeitfenster, in dem abgefragt wird. `None` = rund um die Uhr.
    active_hours: Window | None
    timezone: str
    default_interval: int
    min_interval: int
    per_page: int
    # Zufälliger Zuschlag auf jedes Intervall, damit die Requests kein
    # metronomisches Muster bilden.
    jitter: float
    # Items, die älter sind als das, werden beim ersten Lauf nie gealertet.
    max_item_age: int

    # --- Was überhaupt gemeldet wird ---
    # Nur melden, was mindestens so viel Prozent unter dem Median vergleichbarer
    # Angebote liegt. 0 = alles melden.
    min_discount: float
    # Zeitfenster für die Vergleichspreise, in Tagen.
    price_window_days: int
    # Artikel unter diesem Preis überspringen — meist Kleinkram.
    min_price: float
    # Wörter im Titel, die einen Artikel aussortieren (kaputt, Fälschung …).
    exclude_words: tuple[str, ...]
    # Artikel ohne Foto überspringen — unverkäuflich, und nicht beurteilbar.
    require_photo: bool

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

        zeitzone = os.getenv("TIMEZONE", "").strip() or "Europe/Berlin"
        try:
            fenster = schedule.parse(os.getenv("ACTIVE_HOURS", ""), zeitzone)
        except schedule.InvalidWindow as exc:
            raise SystemExit(str(exc)) from exc

        return cls(
            discord_token=token,
            guild_id=guild_id,
            alert_webhook_url=webhook,
            searches_path=Path(os.getenv("SEARCHES_PATH", "searches.toml")),
            profiles_path=Path(os.getenv("PROFILES_PATH", "profiles.toml")),
            panel_password=os.getenv("PANEL_PASSWORD", "").strip(),
            panel_host=os.getenv("PANEL_HOST", "0.0.0.0").strip() or "0.0.0.0",
            panel_port=_int("PANEL_PORT", 8080),
            extra_countries=_countries("EXTRA_COUNTRIES"),
            dedupe_scope=_choice("DEDUPE_SCOPE", "all", ("group", "all", "watch")),
            alert_mention=_mention("ALERT_MENTION"),
            health_channel_id=_int("HEALTH_CHANNEL", 0),
            health_stale_after=max(60.0, _float("HEALTH_STALE_AFTER", 900.0)),
            health_every=max(60.0, _float("HEALTH_EVERY", 300.0)),
            heartbeat_url=os.getenv("HEARTBEAT_URL", "").strip(),
            alert_retention_hours=max(0, _int("ALERT_RETENTION_HOURS", 0)),
            cleanup_channel_ids=_ids("CLEANUP_CHANNELS"),
            db_path=Path(os.getenv("DB_PATH", "data/sniper.db")),
            polling_enabled=_bool("POLLING", True),
            active_hours=fenster,
            timezone=zeitzone,
            default_interval=default_interval,
            min_interval=min_interval,
            per_page=max(1, min(96, _int("PER_PAGE", 20))),
            jitter=max(0.0, _float("JITTER", 0.25)),
            max_item_age=_int("MAX_ITEM_AGE", 900),
            min_discount=max(0.0, _float("MIN_DISCOUNT", 0.0)),
            price_window_days=max(1, _int("PRICE_WINDOW_DAYS", 30)),
            min_price=max(0.0, _float("MIN_PRICE", 0.0)),
            exclude_words=_words("EXCLUDE_WORDS"),
            require_photo=_bool("REQUIRE_PHOTO", False),
            impersonate=os.getenv("IMPERSONATE", "chrome124").strip() or "chrome124",
            proxies=load_proxies(
                inline=os.getenv("PROXIES", ""),
                path=Path(os.getenv("PROXIES_FILE", "proxies.txt")),
                template=os.getenv("PROXIES_TEMPLATE", "").strip(),
                sessions=_int("PROXIES_SESSIONS", 0),
            ),
            playwright_fallback=_bool("PLAYWRIGHT_FALLBACK", True),
            request_timeout=_float("REQUEST_TIMEOUT", 20.0),
            rate_limit_per_domain=max(1, _int("RATE_LIMIT_PER_DOMAIN", 60)),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
