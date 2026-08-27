"""Slash-Commands zum Anlegen und Verwalten der Suchen."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..db import Watch, serialize_query
from ..vinted import domains
from ..vinted.session import VintedError
from ..vinted.urls import InvalidSearchURL, SearchQuery, parse_search_url
from .. import embeds

if TYPE_CHECKING:
    from .bot import SniperBot


def _default_name(query: SearchQuery, host: str) -> str:
    text = query.scalars.get("search_text")
    if text:
        return text[:80]
    return f"{domains.lookup(host).flag} {host.removeprefix('www.')}"[:80]


async def watch_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Vorschläge für `watch_id`-Optionen — spart das Nachschlagen per `/watch list`."""
    bot: "SniperBot" = interaction.client  # type: ignore[assignment]
    watches = await bot.db.list_watches(interaction.guild_id or 0)
    needle = current.strip().lower()
    choices: list[app_commands.Choice[int]] = []
    for watch in watches:
        label = f"#{watch.id} · {watch.name} ({watch.host.removeprefix('www.')})"
        if needle and needle not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=watch.id))
        if len(choices) == 25:
            break
    return choices


class WatchCommands(commands.Cog):
    """`/watch …` — Suchen anlegen, auflisten, pausieren, löschen."""

    group = app_commands.Group(
        name="watch",
        description="Vinted-Suchen überwachen",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: "SniperBot") -> None:
        self.bot = bot

    # ------------------------------------------------------------------ Helfer

    async def _owned_watch(
        self, interaction: discord.Interaction, watch_id: int
    ) -> Watch | None:
        """Watch laden und sicherstellen, dass sie zu dieser Guild gehört."""
        watch = await self.bot.db.get_watch(watch_id)
        if watch is None or watch.guild_id != (interaction.guild_id or 0):
            await interaction.response.send_message(
                f"Suche #{watch_id} gibt es auf diesem Server nicht.", ephemeral=True
            )
            return None
        return watch

    # --------------------------------------------------------------------- add

    @group.command(name="add", description="Neue Vinted-Suche überwachen")
    @app_commands.describe(
        url="Such-URL von Vinted (Filter auf der Website setzen, dann Adresszeile kopieren)",
        name="Anzeigename für die Suche (optional)",
        channel="Channel für die Alerts (Standard: hier)",
        interval="Prüfintervall in Sekunden (optional)",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        url: str,
        name: str | None = None,
        channel: discord.TextChannel | None = None,
        interval: int | None = None,
    ) -> None:
        settings = self.bot.settings

        try:
            query = parse_search_url(url)
        except InvalidSearchURL as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "Bitte einen normalen Text-Channel angeben.", ephemeral=True
            )
            return

        me = interaction.guild.me if interaction.guild else None
        if me is not None:
            perms = target.permissions_for(me)
            if not (perms.send_messages and perms.embed_links):
                await interaction.response.send_message(
                    f"Mir fehlen in {target.mention} die Rechte **Nachrichten senden** "
                    "und **Links einbetten**.",
                    ephemeral=True,
                )
                return

        poll_interval = max(settings.min_interval, interval or settings.default_interval)

        await interaction.response.defer(thinking=True)

        # Einmal live abfragen: so merkt der Nutzer sofort, ob die URL taugt,
        # statt erst beim ersten stillen Poll-Durchlauf.
        try:
            items = await self.bot.client.search(query)
        except VintedError as exc:
            await interaction.followup.send(
                f"❌ Testabfrage bei `{query.host}` fehlgeschlagen: {exc}"
            )
            return

        watch = await self.bot.db.add_watch(
            guild_id=interaction.guild_id or 0,
            channel_id=target.id,
            creator_id=interaction.user.id,
            name=(name or _default_name(query, query.host)).strip()[:80],
            query=query,
            source_url=query.web_url(),
            interval=poll_interval,
        )
        self.bot.monitor.start(watch)

        await interaction.followup.send(
            embed=embeds.watch_created_embed(watch, len(items))
        )

    # -------------------------------------------------------------------- list

    @group.command(name="list", description="Alle Suchen dieses Servers anzeigen")
    async def list_watches(self, interaction: discord.Interaction) -> None:
        watches = await self.bot.db.list_watches(interaction.guild_id or 0)
        running = {w.id for w in watches if self.bot.monitor.is_running(w.id)}
        await interaction.response.send_message(
            embed=embeds.watch_list_embed(watches, running)
        )

    # ------------------------------------------------------------------ remove

    @group.command(name="remove", description="Suche löschen")
    @app_commands.describe(watch_id="ID der Suche (siehe /watch list)")
    @app_commands.autocomplete(watch_id=watch_id_autocomplete)
    async def remove(self, interaction: discord.Interaction, watch_id: int) -> None:
        watch = await self._owned_watch(interaction, watch_id)
        if watch is None:
            return
        self.bot.monitor.stop(watch_id)
        await self.bot.db.delete_watch(watch_id)
        await interaction.response.send_message(
            f"🗑️ Suche #{watch_id} „{watch.name}“ gelöscht."
        )

    # ------------------------------------------------------------ pause/resume

    @group.command(name="pause", description="Suche vorübergehend anhalten")
    @app_commands.describe(watch_id="ID der Suche (siehe /watch list)")
    @app_commands.autocomplete(watch_id=watch_id_autocomplete)
    async def pause(self, interaction: discord.Interaction, watch_id: int) -> None:
        watch = await self._owned_watch(interaction, watch_id)
        if watch is None:
            return
        self.bot.monitor.stop(watch_id)
        await self.bot.db.set_enabled(watch_id, False)
        await interaction.response.send_message(
            f"⏸️ Suche #{watch_id} „{watch.name}“ pausiert."
        )

    @group.command(name="resume", description="Pausierte Suche fortsetzen")
    @app_commands.describe(watch_id="ID der Suche (siehe /watch list)")
    @app_commands.autocomplete(watch_id=watch_id_autocomplete)
    async def resume(self, interaction: discord.Interaction, watch_id: int) -> None:
        watch = await self._owned_watch(interaction, watch_id)
        if watch is None:
            return
        await self.bot.db.set_enabled(watch_id, True)
        refreshed = await self.bot.db.get_watch(watch_id)
        if refreshed is not None:
            self.bot.monitor.start(refreshed)
        await interaction.response.send_message(
            f"▶️ Suche #{watch_id} „{watch.name}“ läuft wieder."
        )

    # ---------------------------------------------------------------- interval

    @group.command(name="interval", description="Prüfintervall einer Suche ändern")
    @app_commands.describe(
        watch_id="ID der Suche (siehe /watch list)",
        seconds="Neues Intervall in Sekunden",
    )
    @app_commands.autocomplete(watch_id=watch_id_autocomplete)
    async def set_interval(
        self, interaction: discord.Interaction, watch_id: int, seconds: int
    ) -> None:
        watch = await self._owned_watch(interaction, watch_id)
        if watch is None:
            return
        minimum = self.bot.settings.min_interval
        if seconds < minimum:
            await interaction.response.send_message(
                f"Minimum sind {minimum}s — kürzere Abstände provozieren nur "
                "Sperren durch Vinted.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_interval(watch_id, seconds)
        refreshed = await self.bot.db.get_watch(watch_id)
        if refreshed is not None and refreshed.enabled:
            self.bot.monitor.start(refreshed)
        await interaction.response.send_message(
            f"⏱️ Suche #{watch_id} prüft jetzt alle {seconds}s."
        )

    # -------------------------------------------------------------------- test

    @group.command(
        name="test",
        description="Such-URL ausprobieren, ohne eine Überwachung anzulegen",
    )
    @app_commands.describe(url="Such-URL von Vinted")
    async def test(self, interaction: discord.Interaction, url: str) -> None:
        try:
            query = parse_search_url(url)
        except InvalidSearchURL as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            items = await self.bot.client.search(query, per_page=3)
        except VintedError as exc:
            await interaction.followup.send(f"❌ Abfrage fehlgeschlagen: {exc}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send(
                f"Die Suche auf `{query.host}` ({query.describe()}) liefert gerade "
                "keine Treffer. Filter zu eng?",
                ephemeral=True,
            )
            return

        preview = Watch(
            id=0,
            guild_id=interaction.guild_id or 0,
            channel_id=interaction.channel_id or 0,
            creator_id=interaction.user.id,
            name="Test",
            host=query.host,
            source_url=query.web_url(),
            query_json=serialize_query(query),
            interval=0,
            enabled=False,
            created_at=0,
            last_checked_at=None,
            last_error=None,
            hits=0,
        )
        await interaction.followup.send(
            content=(
                f"✅ `{query.host}` antwortet. Filter: **{query.describe()}**\n"
                "So würden die Alerts aussehen:"
            ),
            embeds=[embeds.item_embed(item, preview) for item in items[:3]],
            ephemeral=True,
        )


class StatusCommand(commands.Cog):
    def __init__(self, bot: "SniperBot") -> None:
        self.bot = bot

    @app_commands.command(name="status", description="Zustand des Snipers anzeigen")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        watches = await self.bot.db.list_watches(interaction.guild_id or 0)
        running = {w.id for w in watches if self.bot.monitor.is_running(w.id)}
        await interaction.response.send_message(
            embed=embeds.status_embed(
                watches=watches,
                running=running,
                sessions=self.bot.client.pool.status(),
                started_at=self.bot.started_at,
            )
        )
