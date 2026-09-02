"""Slash-Commands zum Anlegen und Verwalten der Suchen."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..db import Watch, serialize_query
from ..vinted import domains
from ..vinted.session import VintedError
from ..vinted.models import Item
from ..vinted.urls import InvalidSearchURL, SearchQuery, parse_search_url
from .. import bulk, deals, embeds
from . import cleanup

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


class BulkImportModal(discord.ui.Modal, title="Suchen importieren"):
    """Eingabefeld für viele Such-URLs auf einmal.

    Ein Modal statt eines Befehlsparameters: Discord-Optionen sind einzeilig,
    hier braucht es aber ein Feld, in das sich eine ganze Liste einfügen lässt.
    """

    urls: discord.ui.TextInput = discord.ui.TextInput(
        label="Such-URLs — eine je Zeile",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "https://www.vinted.de/catalog?search_text=nike+air+max\n"
            "Carhartt bis 40 | https://www.vinted.de/catalog?search_text=carhartt"
        ),
        max_length=4000,
        required=True,
    )

    def __init__(
        self,
        bot: "SniperBot",
        channel: discord.TextChannel,
        interval: int,
        extra: list[domains.Domain] | None = None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.channel = channel
        self.interval = interval
        self.extra = extra or []

    async def on_submit(self, interaction: discord.Interaction) -> None:
        plan = bulk.parse_import(str(self.urls))
        if not plan.entries and not plan.problems:
            await interaction.response.send_message(
                "Da war keine Adresse dabei.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        # Bewusst ohne Testabfrage je Adresse: bei fünfzig Zeilen wären das
        # fünfzig Abfragen auf einen Schlag, und Vinted sperrt dafür zuverlässig.
        # Eine untaugliche Suche fällt nach dem ersten Durchlauf in `/watch list`
        # als Fehler auf.
        vorhanden = {
            w.source_url for w in await self.bot.db.list_watches(interaction.guild_id or 0)
        }
        angelegt: list[Watch] = []
        bekannt = 0

        for entry in plan.entries:
            queries = bulk.expand(entry.query, self.extra)
            for einzeln in queries:
                url = einzeln.web_url()
                if url in vorhanden:
                    bekannt += 1
                    continue
                vorhanden.add(url)
                watch = await self.bot.db.add_watch(
                    guild_id=interaction.guild_id or 0,
                    channel_id=self.channel.id,
                    creator_id=interaction.user.id,
                    name=(
                        f"{entry.name} {einzeln.domain.flag}"
                        if len(queries) > 1
                        else entry.name
                    )[:80],
                    query=einzeln,
                    source_url=url,
                    interval=self.interval,
                )
                self.bot.monitor.start(watch)
                angelegt.append(watch)

        beschreibung = "\n".join(f"• #{w.id} · {w.name}" for w in angelegt[:15])
        if len(angelegt) > 15:
            beschreibung += f"\n• … und {len(angelegt) - 15} weitere"
        if not angelegt:
            beschreibung = "_Nichts Neues dabei._"

        embed = discord.Embed(
            title=bulk.summarize(plan, angelegt=len(angelegt), bekannt=bekannt),
            description=(
                f"{beschreibung}\n\nAlerts gehen nach {self.channel.mention}, "
                f"geprüft wird alle {self.interval}s."
            ),
            color=embeds.OK_GREEN if angelegt else embeds.WARN_ORANGE,
        )
        if plan.problems:
            fehler = "\n".join(p.describe() for p in plan.problems[:5])
            if len(plan.problems) > 5:
                fehler += f"\n… und {len(plan.problems) - 5} weitere"
            embed.add_field(name="Nicht verwendbar", value=fehler[:1024], inline=False)

        await interaction.followup.send(embed=embed)


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

    def _laender(self, eingabe: str | None) -> tuple[list[domains.Domain], list[str]]:
        """Länderangabe auswerten — ohne Angabe gilt die Voreinstellung.

        `EXTRA_COUNTRIES` in der `.env` legt fest, wo jede Suche zusätzlich
        laufen soll. Wer beim Befehl nichts angibt, bekommt genau das; wer
        etwas angibt, überschreibt es für diesen einen Aufruf. `-` schaltet die
        Voreinstellung für diesen Aufruf ab.
        """
        if eingabe is None or not eingabe.strip():
            return [domains.lookup(h) for h in self.bot.settings.extra_countries], []
        if eingabe.strip() in {"-", "keine", "none"}:
            return [], []
        return domains.parse_list(eingabe)

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
        laender="Zusätzliche Länder, z. B. fr, nl, it — je eine eigene Suche",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        url: str,
        name: str | None = None,
        channel: discord.TextChannel | None = None,
        interval: int | None = None,
        laender: str | None = None,
    ) -> None:
        settings = self.bot.settings

        try:
            query = parse_search_url(url)
        except InvalidSearchURL as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        extra, unbekannt = self._laender(laender)
        if unbekannt:
            await interaction.response.send_message(
                f"❌ Unbekannte Länder: {', '.join(unbekannt)}. "
                "Erlaubt sind Kürzel wie `fr`, `nl`, `it` oder `uk`.",
                ephemeral=True,
            )
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

        basis = (name or _default_name(query, query.host)).strip()[:80]
        queries = bulk.expand(query, extra)
        angelegt: list[Watch] = []
        for einzeln in queries:
            watch = await self.bot.db.add_watch(
                guild_id=interaction.guild_id or 0,
                channel_id=target.id,
                creator_id=interaction.user.id,
                name=(f"{basis} {einzeln.domain.flag}" if len(queries) > 1 else basis)[:80],
                query=einzeln,
                source_url=einzeln.web_url(),
                interval=poll_interval,
            )
            self.bot.monitor.start(watch)
            angelegt.append(watch)

        if len(angelegt) == 1:
            await interaction.followup.send(
                embed=embeds.watch_created_embed(angelegt[0], len(items))
            )
            return

        zeilen = "\n".join(
            f"• #{w.id} · {w.name}" for w in angelegt
        )
        embed = discord.Embed(
            title=f"{len(angelegt)} Suchen angelegt",
            description=(
                f"{zeilen}\n\nJedes Land bekommt einen eigenen Bestand — derselbe "
                "Artikel in zwei Ländern meldet also zweimal, und ein Fund in "
                "Italien verschluckt den in Frankreich nicht."
            ),
            color=embeds.OK_GREEN,
        )
        hinweis = bulk.currency_warning(query, extra)
        if hinweis:
            embed.add_field(name="Währung", value=hinweis, inline=False)
        await interaction.followup.send(embed=embed)

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

    # -------------------------------------------------------------------- bulk

    @group.command(
        name="bulk",
        description="Viele Such-URLs auf einmal anlegen — eine je Zeile",
    )
    @app_commands.describe(
        channel="Channel für die Alerts (Standard: hier)",
        interval="Prüfintervall in Sekunden für alle (optional)",
        laender="Zusätzliche Länder für jede Zeile, z. B. fr, nl, it",
    )
    async def bulk_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        interval: int | None = None,
        laender: str | None = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "Bitte einen normalen Text-Channel angeben.", ephemeral=True
            )
            return

        extra, unbekannt = self._laender(laender)
        if unbekannt:
            await interaction.response.send_message(
                f"❌ Unbekannte Länder: {', '.join(unbekannt)}. "
                "Erlaubt sind Kürzel wie `fr`, `nl`, `it` oder `uk`.",
                ephemeral=True,
            )
            return

        settings = self.bot.settings
        poll_interval = max(settings.min_interval, interval or settings.default_interval)
        # Das Modal muss die erste Antwort auf die Interaktion sein — vorher darf
        # nichts gesendet oder deferred werden.
        await interaction.response.send_modal(
            BulkImportModal(self.bot, target, poll_interval, extra)
        )

    # ------------------------------------------------------------------ import

    @group.command(
        name="import",
        description="Suchen aus searches.toml übernehmen und hier verwaltbar machen",
    )
    @app_commands.describe(channel="Channel für die Alerts (Standard: hier)")
    async def import_file_searches(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "Bitte einen normalen Text-Channel angeben.", ephemeral=True
            )
            return

        file_watches = await self.bot.db.list_file_watches()
        if not file_watches:
            await interaction.response.send_message(
                "Es gibt keine Suchen aus `searches.toml` zu übernehmen — hier "
                "läuft schon alles über Slash-Commands.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        for watch in file_watches:
            await self.bot.db.adopt_file_watch(
                watch.id,
                guild_id=interaction.guild_id or 0,
                channel_id=target.id,
                creator_id=interaction.user.id,
            )
            refreshed = await self.bot.db.get_watch(watch.id)
            if refreshed is not None and refreshed.enabled:
                # Neu starten, damit die Zustellung sofort über den Bot läuft.
                self.bot.monitor.start(refreshed)

        names = "\n".join(f"• #{w.id} · {w.name}" for w in file_watches[:20])
        if len(file_watches) > 20:
            names += f"\n• … und {len(file_watches) - 20} weitere"

        embed = discord.Embed(
            title=f"{len(file_watches)} Suche(n) übernommen",
            description=(
                f"{names}\n\nSie melden ab sofort in {target.mention} und lassen "
                "sich mit `/watch` verwalten. Trefferhistorie und Intervalle "
                "bleiben erhalten."
            ),
            color=embeds.OK_GREEN,
        )
        embed.set_footer(
            text="Leere jetzt searches.toml, sonst werden sie beim nächsten "
            "Neustart erneut als Datei-Suchen angelegt."
        )
        await interaction.followup.send(embed=embed)

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


class EvaluateCommand(commands.Cog):
    """`/pruefen` — einen Fund bewerten, ohne Vinted abzufragen.

    Der Weg ohne automatisches Abfragen: Vinted schickt seine eigenen
    Benachrichtigungen zu gespeicherten Suchen, du gibst die Eckdaten hier ein,
    und der Bot rechnet Marge, Rendite und maximalen Einkaufspreis aus. Es geht
    dabei keine einzige Anfrage an Vinted raus — gerechnet wird mit dem, was in
    der Benachrichtigung steht.
    """

    def __init__(self, bot: "SniperBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="pruefen",
        description="Einen Fund durchrechnen — Ampel, Marge, maximaler Einkaufspreis",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        titel="Titel des Angebots (entscheidet, welches Kaufprofil greift)",
        preis="Artikelpreis in Euro",
        groesse="Größe laut Angebot (M, L, XL …)",
        zustand="Zustand laut Angebot (z. B. Sehr gut)",
        checkout="Tatsächlicher Checkout-Gesamtpreis, falls schon bekannt",
        url="Link zum Angebot (optional, nur fürs Protokoll)",
    )
    async def pruefen(
        self,
        interaction: discord.Interaction,
        titel: str,
        preis: float,
        groesse: str | None = None,
        zustand: str | None = None,
        checkout: float | None = None,
        url: str | None = None,
    ) -> None:
        profile = self.bot.monitor.profiles
        if not profile:
            await interaction.response.send_message(
                "Es sind keine Kaufprofile hinterlegt. Lege `profiles.toml` an "
                "(Vorlage: `profiles.example.toml`), sonst gibt es nichts zu "
                "rechnen.",
                ephemeral=True,
            )
            return

        fund = Item(
            id=url or titel,
            host="www.vinted.de",
            title=titel,
            url=url or "",
            price=preis,
            total_price=None,
            currency="EUR",
            brand=None,
            size=groesse,
            condition=zustand,
            photo_url=None,
            seller=None,
            seller_url=None,
            favourites=0,
            views=0,
            posted_ts=None,
        )

        # Der abgelesene Checkout-Betrag ist der ganze Betrag — Artikel,
        # Versand, Käuferschutz. Er ersetzt die Schätzung komplett.
        urteil = deals.best_verdict(fund, profile, checkout_total=checkout)
        if urteil is None:
            namen = ", ".join(p.name for p in profile)
            await interaction.response.send_message(
                f"„{titel}“ passt auf keins deiner Profile ({namen}). Steht die "
                "Produktart im Titel?",
                ephemeral=True,
            )
            return

        grenze = deals.max_buy_price(urteil.profile)
        embed = discord.Embed(
            title=f"{titel[:200]}",
            url=url or None,
            description=urteil.headline(),
            color=(
                embeds.DEAL_GREEN
                if urteil.grade == deals.GREEN
                else embeds.DEAL_AMBER
                if urteil.grade == deals.YELLOW
                else embeds.WARN_ORANGE
            ),
        )
        if urteil.notes:
            embed.add_field(
                name="Was dagegen spricht",
                value="\n".join(f"• {n}" for n in urteil.notes),
                inline=False,
            )
        rechnung = urteil.breakdown()
        if rechnung:
            embed.add_field(name="Rechnung", value=rechnung, inline=False)
        embed.add_field(
            name="Dein maximaler Artikelpreis",
            value=(
                f"**{grenze:.2f} €** für „{urteil.profile.name}“ — darüber "
                f"bleibt weniger als {urteil.profile.thresholds.min_profit:.0f} € "
                "Gewinn übrig."
            ).replace(".", ",", 1),
            inline=False,
        )
        if checkout is None:
            embed.add_field(
                name="Noch offen",
                value=(
                    "Gerechnet wurde mit **geschätzten** Versand- und "
                    "Käuferschutzkosten. Leg den Artikel in den Vinted-Checkout, "
                    "lies den echten Gesamtbetrag ab und ruf `/pruefen` mit "
                    "`checkout:` noch einmal auf."
                ),
                inline=False,
            )
        embed.add_field(
            name="Vor dem Kauf prüfen",
            value="\n".join(f"☐ {punkt}" for punkt in deals.CHECKLIST),
            inline=False,
        )
        embed.add_field(
            name="Wirklich verkaufte Vergleichsartikel",
            value=f"[bei eBay nachsehen]({deals.ebay_sold_url(fund)})",
            inline=False,
        )
        if urteil.grade == deals.YELLOW:
            embed.add_field(
                name="Nachricht an den Verkäufer",
                value=f"```\n{deals.SELLER_QUESTION}\n```",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


class ChannelCommands(commands.Cog):
    """Aufräumen im Alert-Channel."""

    def __init__(self, bot: "SniperBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="clear", description="Nachrichten in diesem Channel löschen"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        anzahl="Wie viele der letzten Nachrichten (Standard 100)",
        alles="Alles löschen, was der Bot erreicht — ignoriert die Anzahl",
        aelter_als="Nur löschen, was älter ist als … Stunden",
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        anzahl: int = 100,
        alles: bool = False,
        aelter_als: int | None = None,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Das geht nur in einem normalen Text-Channel.", ephemeral=True
            )
            return

        if not alles and anzahl < 1:
            await interaction.response.send_message(
                "Die Anzahl muss mindestens 1 sein.", ephemeral=True
            )
            return

        me = interaction.guild.me if interaction.guild else None
        if not cleanup.may_purge(channel, me):
            await interaction.response.send_message(
                "Mir fehlen in diesem Channel die Rechte **Nachrichten verwalten** "
                "und **Nachrichtenverlauf anzeigen**. Ohne die darf ich nichts löschen.",
                ephemeral=True,
            )
            return

        limit = cleanup.MAX_PER_RUN if alles else min(anzahl, cleanup.MAX_PER_RUN)

        # Ephemeral, damit die Bestätigung nicht selbst im aufgeräumten Channel
        # stehen bleibt.
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            if aelter_als and aelter_als > 0:
                geloescht = await cleanup.purge_older_than(
                    channel, hours=aelter_als, limit=limit
                )
                womit = f"älter als {aelter_als} h"
            else:
                geloescht = await cleanup.purge(channel, limit=limit)
                womit = "alles" if alles else f"die letzten {anzahl}"
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Löschen fehlgeschlagen: {exc}", ephemeral=True
            )
            return

        hinweis = ""
        if geloescht >= cleanup.MAX_PER_RUN:
            hinweis = (
                f"\n\nDas war der Deckel von {cleanup.MAX_PER_RUN} pro Aufruf. "
                "Ruf den Befehl noch einmal auf, wenn noch etwas übrig ist."
            )
        elif alles and geloescht == 0:
            hinweis = (
                "\n\nNichts gelöscht. Meist heißt das: alles ist älter als 14 Tage. "
                "So alte Nachrichten lässt Discord nur einzeln löschen, was ewig "
                "dauert. Schneller ist dann Rechtsklick auf den Channel → **Kanal "
                "duplizieren**, danach den alten löschen."
            )
        await interaction.followup.send(
            f"🧹 {geloescht} Nachricht(en) gelöscht ({womit}).{hinweis}", ephemeral=True
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
                meter=self.bot.client.pool.meter,
                window=self.bot.monitor.window,
            )
        )
