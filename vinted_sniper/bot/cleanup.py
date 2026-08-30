"""Alert-Channels wieder aufräumen.

Ein Sniper-Channel läuft schnell voll — mehrere hundert Alerts am Tag sind
normal. Diese Funktionen löschen in Blöcken und mit Pausen dazwischen, statt
alles auf einmal zu versuchen: Discord drosselt Löschvorgänge hart, und ein zu
gieriger Aufruf bringt den Bot für Minuten zum Stehen.

Zwei Grenzen kommen von Discord, nicht von hier:
  * Pro Aufruf lassen sich höchstens 100 Nachrichten auf einmal löschen.
  * Nachrichten älter als 14 Tage gehen nur einzeln — das dauert spürbar.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord

log = logging.getLogger(__name__)

# Discords Obergrenze für einen Sammel-Löschvorgang.
BLOCK = 100
# Pause zwischen zwei Blöcken. Ohne die läuft der Bot ins Ratelimit und wird
# für alle anderen Aktionen mit ausgebremst.
PAUSE = 1.0
# Deckel je Durchlauf. Ein frisch eingeschaltetes Aufräumen soll nicht
# stundenlang durch die gesamte Channel-Historie laufen.
MAX_PER_RUN = 2000


async def purge(
    channel: discord.TextChannel,
    *,
    before: dt.datetime | None = None,
    limit: int = MAX_PER_RUN,
) -> int:
    """In Blöcken löschen und die Anzahl zurückgeben.

    `before` grenzt auf Nachrichten ein, die älter sind als dieser Zeitpunkt;
    ohne wird von den neuesten an gelöscht.
    """
    geloescht = 0
    while geloescht < limit:
        block = await channel.purge(limit=min(BLOCK, limit - geloescht), before=before)
        geloescht += len(block)
        if len(block) < BLOCK:
            # Weniger als ein voller Block heißt: mehr gibt es nicht.
            break
        await asyncio.sleep(PAUSE)
    return geloescht


async def purge_older_than(
    channel: discord.TextChannel, *, hours: int, limit: int = MAX_PER_RUN
) -> int:
    cutoff = discord.utils.utcnow() - dt.timedelta(hours=hours)
    return await purge(channel, before=cutoff, limit=limit)


def may_purge(channel: discord.TextChannel, me: discord.Member | None) -> bool:
    """Darf der Bot in diesem Channel löschen?"""
    if me is None:
        return True
    rechte = channel.permissions_for(me)
    return rechte.manage_messages and rechte.read_message_history
