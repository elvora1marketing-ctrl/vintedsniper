"""Einstiegspunkt: `python -m vinted_sniper`."""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from .bot import SniperBot
from .config import Settings


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # discord.py ist auf INFO sehr geschwätzig; Warnungen reichen.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def run() -> int:
    settings = Settings.load()
    setup_logging(settings.log_level)
    log = logging.getLogger("vinted_sniper")

    bot = SniperBot(settings)
    try:
        await bot.start(settings.discord_token)
    except discord.LoginFailure:
        log.error("Discord lehnt das Token ab. Stimmt DISCORD_TOKEN in der .env?")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Discord verlangt Intents, die im Developer Portal nicht aktiviert sind."
        )
        return 1
    finally:
        if not bot.is_closed():
            await bot.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
