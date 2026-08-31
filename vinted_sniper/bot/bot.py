"""Discord-Bot: hält Monitor und Datenbank zusammen und stellt Alerts zu."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import commands

from .. import embeds
from . import cleanup
from ..config import Settings
from ..db import Database, Watch
from ..monitor import Monitor
from ..notifiers import WebhookNotifier
from ..panel.app import PanelServer
from ..profiles import InvalidProfileFile, load_profiles
from ..searches import InvalidSearchFile, load_searches, sync_to_db
from ..vinted.client import VintedClient
from ..watchdog import Watchdog
from ..vinted.models import Item

log = logging.getLogger(__name__)

# Discord erlaubt ~5 Nachrichten pro 5s je Channel. Wir bleiben bewusst
# darunter, sonst wird der Bot ausgebremst und Alerts kommen verspätet.
SEND_DELAY = 0.7
# Wenn ein Poll-Durchlauf mehr liefert, stimmt meist der Filter nicht — dann
# eine Sammelmeldung statt hundert Einzelposts.
MAX_ALERTS_PER_ROUND = 10
# Wie oft nach abgelaufenen Alerts geschaut wird. Halbstündlich reicht: die
# Aufbewahrungsdauer ist in Stunden angegeben, minutengenau muss das nicht sein.
CLEANUP_EVERY = 1800


class SniperBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        # Der Bot liest keine Nachrichteninhalte — Slash-Commands reichen.
        super().__init__(command_prefix="!vs ", intents=intents, help_command=None)

        self.settings = settings
        self.db = Database(settings.db_path)
        self.client = VintedClient(settings)
        # Auch im Bot-Modus kann eine Suche ein Webhook-Ziel haben — etwa eine
        # aus `searches.toml` oder ein Channel, in dem der Bot nicht ist.
        self.webhooks = WebhookNotifier()
        self.monitor = Monitor(
            settings,
            self.db,
            self.client,
            on_items=self._deliver_items,
            on_trouble=self._deliver_trouble,
            on_recovered=self._deliver_recovered,
        )
        self.started_at = dt.datetime.now(dt.timezone.utc)
        # Dasselbe Panel wie im Webhook-Modus: gleiche Datenbank, gleicher
        # Monitor. Was dort geändert wird, wirkt sofort auch hier.
        self.panel = PanelServer(
            db=self.db,
            monitor=self.monitor,
            client=self.client,
            password=settings.panel_password,
            alert_webhook_url=settings.alert_webhook_url,
            host=settings.panel_host,
            port=settings.panel_port,
            min_interval=settings.min_interval,
            default_interval=settings.default_interval,
            started_at=self.started_at,
            default_countries=settings.extra_countries,
        )
        self.watchdog = Watchdog(settings, self.db, send=self._deliver_health)
        self._send_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        # `on_ready` feuert auch nach jedem Reconnect — der Startlauf darf aber
        # nur einmal passieren.
        self._bootstrapped = False

    # ------------------------------------------------------------- Lebenszyklus

    async def setup_hook(self) -> None:
        await self.db.connect()
        # Das Panel bewusst als Erstes: es hängt nur an Datenbank und Monitor.
        # Käme es später, wäre es so lange nicht erreichbar, wie Discord beim
        # Registrieren der Befehle braucht — und wenn Discord dabei mit 429
        # bremst, sind das Minuten, in denen ein Reverse-Proxy nur 502 liefert.
        await self.panel.start()

        from .commands import ChannelCommands, StatusCommand, WatchCommands

        await self.add_cog(WatchCommands(self))
        await self.add_cog(StatusCommand(self))
        await self.add_cog(ChannelCommands(self))

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash-Commands für Guild %s registriert.", self.settings.guild_id)

    async def _register_commands(self) -> None:
        """Slash-Commands registrieren, wenn keine Guild-ID konfiguriert ist.

        Ohne Guild-ID würde eine globale Registrierung bis zu einer Stunde
        brauchen, bis die Befehle auftauchen. Da hier erst nach dem Verbinden
        bekannt ist, auf welchen Servern der Bot überhaupt ist, wird das
        nachgeholt: pro Server registriert, sind die Befehle sofort da — und
        niemand muss eine Server-ID heraussuchen.
        """
        if self.settings.guild_id or not self.guilds:
            if not self.guilds:
                log.warning(
                    "Der Bot ist auf keinem Server. Lade ihn über den "
                    "OAuth2-Link ein, dann tauchen die Befehle auf."
                )
            return

        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash-Commands für „%s“ registriert.", guild.name)

    def _load_profiles(self) -> None:
        """Kaufprofile einlesen.

        Eine kaputte Datei wird gemeldet und der Sniper läuft ohne Profile
        weiter — er meldet dann mehr statt weniger. Andersherum (stumm bleiben)
        wäre der teurere Fehler.
        """
        try:
            self.monitor.profiles = load_profiles(self.settings.profiles_path)
        except InvalidProfileFile as exc:
            log.error("%s wird ignoriert: %s", self.settings.profiles_path, exc)
            return
        if self.monitor.profiles:
            log.info(
                "%d Kaufprofil(e) aktiv: %s",
                len(self.monitor.profiles),
                ", ".join(p.name for p in self.monitor.profiles),
            )

    async def _sync_file_searches(self) -> None:
        """`searches.toml` mitlaufen lassen, falls vorhanden.

        So funktionieren beide Wege nebeneinander: was in der Datei steht, läuft
        über den Webhook; was per `/watch add` angelegt wurde, über den Bot.
        """
        try:
            file_searches = load_searches(
                self.settings.searches_path,
                default_interval=self.settings.default_interval,
                min_interval=self.settings.min_interval,
                default_webhook=self.settings.alert_webhook_url,
                # Im Bot-Modus werden Suchen per Slash-Command verwaltet — eine
                # leere oder fehlende Datei ist hier völlig normal.
                allow_empty=True,
                extra_countries=self.settings.extra_countries,
            )
        except InvalidSearchFile as exc:
            log.error("%s wird ignoriert: %s", self.settings.searches_path, exc)
            return
        watches = await sync_to_db(self.db, file_searches)
        if watches:
            log.info(
                "%d Suche(n) aus %s übernommen.",
                len(watches),
                self.settings.searches_path,
            )

    async def on_ready(self) -> None:
        log.info("Eingeloggt als %s (ID %s)", self.user, getattr(self.user, "id", "?"))
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="Vinted-Neuheiten"
            )
        )
        if self._bootstrapped:
            return
        self._bootstrapped = True

        await self._register_commands()
        self._load_profiles()
        await self._sync_file_searches()
        started = await self.monitor.start_all()
        log.info("%d gespeicherte Suchen gestartet.", started)

        await self.watchdog.report_downtime()
        self.watchdog.start()

        if self.settings.alert_retention_hours:
            self._cleanup_task = self.loop.create_task(self._cleanup_loop())

    # -------------------------------------------------------------- Aufräumen

    async def _cleanup_targets(self) -> list[discord.TextChannel]:
        """Welche Channels aufgeräumt werden.

        Ohne feste Angabe alle, in die der Bot selbst alertet. Suchen mit
        Webhook-Ziel bleiben außen vor: aus einer Webhook-URL lässt sich der
        Channel nicht ableiten — dafür muss die ID in `CLEANUP_CHANNELS` stehen.
        """
        ids = list(self.settings.cleanup_channel_ids)
        if not ids:
            ids = [
                w.channel_id
                for w in await self.db.list_watches()
                if w.channel_id and not w.webhook_url
            ]

        channels: list[discord.TextChannel] = []
        for channel_id in dict.fromkeys(ids):
            channel = await self._resolve_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                channels.append(channel)
        return channels

    async def _cleanup_once(self) -> None:
        stunden = self.settings.alert_retention_hours
        for channel in await self._cleanup_targets():
            me = channel.guild.me
            if not cleanup.may_purge(channel, me):
                log.warning(
                    "Kein Aufräumen in #%s: mir fehlen dort die Rechte "
                    "„Nachrichten verwalten“ und „Nachrichtenverlauf anzeigen“.",
                    channel.name,
                )
                continue
            try:
                geloescht = await cleanup.purge_older_than(channel, hours=stunden)
            except discord.HTTPException as exc:
                log.warning("Aufräumen in #%s fehlgeschlagen: %s", channel.name, exc)
                continue
            if geloescht:
                log.info(
                    "%d Nachricht(en) älter als %dh in #%s gelöscht.",
                    geloescht,
                    stunden,
                    channel.name,
                )

    async def _cleanup_loop(self) -> None:
        await self.wait_until_ready()
        log.info(
            "Automatisches Aufräumen aktiv: Alerts älter als %dh werden gelöscht.",
            self.settings.alert_retention_hours,
        )
        while not self.is_closed():
            try:
                await self._cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Aufräumen ist Nebensache — ein Fehler hier darf den Sniper
                # nicht mitreißen.
                log.exception("Aufräumen fehlgeschlagen.")
            await asyncio.sleep(CLEANUP_EVERY)

    async def _health_target(self) -> discord.abc.Messageable | None:
        """Wohin Ausfallmeldungen gehen.

        Fest eingestellter Channel, sonst der einer beliebigen Suche. Wichtig
        ist, dass es überhaupt ein Ziel gibt: eine Ausfallmeldung, die niemand
        sieht, ist wertlos.
        """
        if self.settings.health_channel_id:
            return await self._resolve_channel(self.settings.health_channel_id)
        for watch in await self.db.list_watches():
            if watch.channel_id and not watch.webhook_url:
                return await self._resolve_channel(watch.channel_id)
        return None

    async def _deliver_health(self, titel: str, text: str, alarm: bool) -> None:
        """Ausfallmeldung zustellen — mit Erwähnung, wenn es ernst ist."""
        embed = discord.Embed(
            title=titel,
            description=text,
            color=embeds.WARN_ORANGE if alarm else embeds.OK_GREEN,
            timestamp=dt.datetime.now(dt.timezone.utc),
        )
        erwaehnung = self.settings.alert_mention if alarm else ""

        channel = await self._health_target()
        if channel is not None:
            await channel.send(
                content=erwaehnung or None,
                embed=embed,
                # Ohne das ignoriert Discord die Erwähnung stillschweigend.
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=True, everyone=True
                ),
            )
            return

        if self.settings.alert_webhook_url:
            await self.webhooks.send_health(
                self.settings.alert_webhook_url, embed, erwaehnung
            )
            return

        log.error(
            "Ausfallmeldung „%s“ konnte nirgends zugestellt werden: weder "
            "HEALTH_CHANNEL noch ALERT_WEBHOOK_URL ist gesetzt.",
            titel,
        )

    async def close(self) -> None:
        self.watchdog.stop()
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
        await self.monitor.shutdown()
        await self.panel.close()
        await self.webhooks.close()
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
        # Suchen mit eigenem Webhook-Ziel (z. B. aus `searches.toml`) gehen nicht
        # über den Bot — der ist in dem Channel womöglich gar nicht.
        if watch.webhook_url:
            await self.webhooks.send_items(watch, items)
            return

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
        if watch.webhook_url:
            await self.webhooks.send_trouble(watch, message)
            return
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
        if watch.webhook_url:
            await self.webhooks.send_recovered(watch, message)
            return
        channel = await self._resolve_channel(watch.channel_id)
        if channel is None:
            return
        embed = discord.Embed(
            title=f"Suche #{watch.id} „{watch.name}“ läuft wieder",
            description=message,
            color=embeds.OK_GREEN,
        )
        await channel.send(embed=embed)
