"""Webhook-Modus: Sniper ohne Bot-Token, Suchen aus `searches.toml`.

Hier gibt es keine Discord-Verbindung im eigentlichen Sinn — nur ausgehende
Webhook-Aufrufe. Entsprechend gibt es auch keine Slash-Commands: die Datei ist
die einzige Steuerung.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord

from . import embeds

from .config import Settings
from .db import Database, DatabaseUnavailable
from .monitor import Monitor
from .notifiers import WebhookNotifier
from .panel.app import PanelServer
from .profiles import InvalidProfileFile, load_profiles
from .searches import InvalidSearchFile, load_searches, sync_to_db
from .vinted.client import VintedClient
from .watchdog import Watchdog

log = logging.getLogger(__name__)


async def run_webhook_mode(settings: Settings) -> int:
    try:
        file_searches = load_searches(
            settings.searches_path,
            default_interval=settings.default_interval,
            min_interval=settings.min_interval,
            default_webhook=settings.alert_webhook_url,
            extra_countries=settings.extra_countries,
        )
    except InvalidSearchFile as exc:
        log.error("%s", exc)
        return 1

    try:
        profile = load_profiles(settings.profiles_path)
    except InvalidProfileFile as exc:
        log.error("%s", exc)
        return 1
    if profile:
        log.info(
            "%d Kaufprofil(e) aktiv: %s",
            len(profile),
            ", ".join(p.name for p in profile),
        )

    db = Database(settings.db_path)
    try:
        await db.connect()
    except DatabaseUnavailable as exc:
        log.error("%s", exc)
        return 1

    watchdog = Watchdog(settings, db, send=lambda *_: asyncio.sleep(0))
    client = VintedClient(settings)
    notifier = WebhookNotifier()
    monitor = Monitor(
        settings,
        db,
        client,
        on_items=notifier.send_items,
        on_trouble=notifier.send_trouble,
        on_recovered=notifier.send_recovered,
    )

    panel = PanelServer(
        db=db,
        monitor=monitor,
        client=client,
        password=settings.panel_password,
        alert_webhook_url=settings.alert_webhook_url,
        host=settings.panel_host,
        port=settings.panel_port,
        min_interval=settings.min_interval,
        default_interval=settings.default_interval,
        started_at=dt.datetime.now(dt.timezone.utc),
        default_countries=settings.extra_countries,
    )

    try:
        watches = await sync_to_db(db, file_searches)
        log.info("%d Suche(n) aus %s übernommen.", len(watches), settings.searches_path)

        if settings.alert_webhook_url:
            # Bestätigt dem Nutzer im Channel, dass der Webhook stimmt — ohne
            # das wüsste er erst beim ersten Treffer, ob überhaupt etwas ankommt.
            await notifier.send_startup(settings.alert_webhook_url, watches)

        async def melde_zustand(titel: str, text: str, alarm: bool) -> None:
            """Ausfallmeldung über den Webhook — mit Erwähnung, wenn es ernst ist."""
            if not settings.alert_webhook_url:
                log.error("Ausfallmeldung „%s“ ohne Ziel: %s", titel, text)
                return
            await notifier.send_health(
                settings.alert_webhook_url,
                discord.Embed(
                    title=titel,
                    description=text,
                    color=embeds.WARN_ORANGE if alarm else embeds.OK_GREEN,
                ),
                settings.alert_mention if alarm else "",
            )

        watchdog = Watchdog(settings, db, send=melde_zustand)
        await watchdog.report_downtime()
        watchdog.start()

        await panel.start()
        monitor.profiles = profile
        started = await monitor.start_all()
        log.info("%d Suche(n) laufen. Beenden mit Strg-C.", started)

        # Der Prozess lebt nur für die Monitor-Tasks; hier warten wir auf das
        # Signal von außen.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        log.info("Abbruch empfangen.")
    finally:
        watchdog.stop()
        await monitor.shutdown()
        await panel.close()
        await notifier.close()
        await db.close()

    return 0
