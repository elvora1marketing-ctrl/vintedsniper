"""Einstiegspunkt: `python -m vinted_sniper`.

Der Modus ergibt sich aus der Konfiguration: mit `DISCORD_TOKEN` startet der
volle Bot samt Slash-Commands, ohne Token läuft der Webhook-Modus mit Suchen
aus `searches.toml`. Beides teilt sich denselben Kern.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from .config import Mode, Settings
from .db import DatabaseUnavailable


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # discord.py ist auf INFO sehr geschwätzig; Warnungen reichen.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def run_bot(settings: Settings) -> int:
    from .bot import SniperBot

    log = logging.getLogger("vinted_sniper")
    bot = SniperBot(settings)
    try:
        await bot.start(settings.discord_token)
    except discord.LoginFailure:
        log.error("Discord lehnt das Token ab. Stimmt DISCORD_TOKEN in der .env?")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error("Discord verlangt Intents, die im Developer Portal fehlen.")
        return 1
    except DatabaseUnavailable as exc:
        log.error("%s", exc)
        return 1
    finally:
        if not bot.is_closed():
            await bot.close()
    return 0


async def run() -> int:
    settings = Settings.load()
    setup_logging(settings.log_level)
    log = logging.getLogger("vinted_sniper")

    if settings.mode is Mode.BOT:
        log.info("Starte im Bot-Modus (Slash-Commands aktiv).")
        return await run_bot(settings)

    from .runner import run_webhook_mode

    log.info(
        "Starte im Webhook-Modus — kein Bot-Token gesetzt, Suchen kommen aus %s.",
        settings.searches_path,
    )
    return await run_webhook_mode(settings)


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
