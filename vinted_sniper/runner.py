"""Webhook-Modus: Sniper ohne Bot-Token, Suchen aus `searches.toml`.

Hier gibt es keine Discord-Verbindung im eigentlichen Sinn — nur ausgehende
Webhook-Aufrufe. Entsprechend gibt es auch keine Slash-Commands: die Datei ist
die einzige Steuerung.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from .config import Settings
from .db import Database, DatabaseUnavailable
from .monitor import Monitor
from .notifiers import WebhookNotifier
from .panel.app import PanelServer
from .searches import InvalidSearchFile, load_searches, sync_to_db
from .vinted.client import VintedClient

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

    db = Database(settings.db_path)
    try:
        await db.connect()
    except DatabaseUnavailable as exc:
        log.error("%s", exc)
        return 1

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

        await panel.start()
        started = await monitor.start_all()
        log.info("%d Suche(n) laufen. Beenden mit Strg-C.", started)

        # Der Prozess lebt nur für die Monitor-Tasks; hier warten wir auf das
        # Signal von außen.
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        log.info("Abbruch empfangen.")
    finally:
        await monitor.shutdown()
        await panel.close()
        await notifier.close()
        await db.close()

    return 0
