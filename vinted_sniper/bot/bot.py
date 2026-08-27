"""Discord-Bot: hält Monitor und Datenbank zusammen und stellt Alerts zu."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import commands

from ..config import Settings
from ..db import Database, Watch
from ..monitor import Monitor
from ..vinted.client import VintedClient
from ..vinted.models import Item
from . import embeds

log = logging.getLogger(__name__)

# Discord erlaubt ~5 Nachrichten pro 5s je Channel. Wir bleiben bewusst
# darunter, sonst wird der Bot ausgebremst und Alerts kommen verspätet.
SEND_DELAY = 0.7
# Wenn ein Poll-Durchlauf mehr liefert, stimmt meist der Filter nicht — dann
# eine Sammelmeldung statt hundert Einzelposts.
MAX_ALERTS_PER_ROUND = 10


class SniperBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        # Der Bot liest keine Nachrichteninhalte — Slash-Commands reichen.
        super().__init__(command_prefix="!vs ", intents=intents, help_command=None)

        self.settings = settings
        self.db = Database(settings.db_path)
        self.client = VintedClient(settings)
        self.monitor = Monitor(
            settings,
            self.db,
            self.client,
            on_items=self._deliver_items,
            on_trouble=self._deliver_trouble,
            on_recovered=self._deliver_recovered,
        )
        self.started_at = dt.datetime.now(dt.timezone.utc)
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------- Lebenszyklus

    async def setup_hook(self) -> None:
        await self.db.connect()

        from .commands import StatusCommand, WatchCommands

        await self.add_cog(WatchCommands(self))
        await self.add_cog(StatusCommand(self))

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash-Commands für Guild %s registriert.", self.settings.guild_id)
        else:
            await self.tree.sync()
            log.info("Slash-Commands global registriert (Propagation dauert bis zu 1h).")

    async def on_ready(self) -> None:
        log.info("Eingeloggt als %s (ID %s)", self.user, getattr(self.user, "id", "?"))
        started = await self.monitor.start_all()
        log.info("%d gespeicherte Suchen wieder gestartet.", started)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="Vinted-Neuheiten"
            )
        )

    async def close(self) -> None:
        await self.monitor.shutdown()
        await self.db.close()
        await super().close()

    # ---------------------------------------------------------------- Zustellung

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("Channel %s nicht erreichbar: %s", channel_id, exc)
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _deliver_items(self, watch: Watch, items: list[Item]) -> None:
        channel = await self._resolve_channel(watch.channel_id)
        if channel is None:
            return

        overflow = max(0, len(items) - MAX_ALERTS_PER_ROUND)
        # Der Lock serialisiert alle Watches, damit sich parallele Treffer nicht
        # gegenseitig ins Discord-Ratelimit schieben.
        async with self._send_lock:
            for item in items[:MAX_ALERTS_PER_ROUND]:
                try:
                    await channel.send(
                        embed=embeds.item_embed(item, watch), view=embeds.item_view(item)
                    )
                except discord.HTTPException as exc:
                    log.error("Alert für Item %s fehlgeschlagen: %s", item.id, exc)
                await asyncio.sleep(SEND_DELAY)

            if overflow:
                await channel.send(
                    f"… und **{overflow}** weitere Treffer für Suche "
                    f"#{watch.id} „{watch.name}“. Die Suche ist vermutlich zu weit "
                    "gefasst — enger filtern lohnt sich."
                )

    async def _deliver_trouble(self, watch: Watch, message: str) -> None:
        channel = await self._resolve_channel(watch.channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title=f"Suche #{watch.id} „{watch.name}“ hakt",
            description=(
                f"{message}\n\nDer Bot versucht es weiter mit größeren Abständen "
                "und baut die Session automatisch neu auf."
            ),
            color=embeds.WARN_ORANGE,
        )
        await channel.send(embed=embed)

    async def _deliver_recovered(self, watch: Watch, message: str) -> None:
        channel = await self._resolve_channel(watch.channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title=f"Suche #{watch.id} „{watch.name}“ läuft wieder",
            description=message,
            color=embeds.OK_GREEN,
        )
        await channel.send(embed=embed)
