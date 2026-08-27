"""Aufbereitung von Listings und Watches als Discord-Embeds."""

from __future__ import annotations

import datetime as dt

import discord

from ..db import Watch
from ..vinted import domains
from ..vinted.models import Item

VINTED_TEAL = 0x09B1BA
WARN_ORANGE = 0xE67E22
OK_GREEN = 0x2ECC71


def _age_label(item: Item) -> str:
    age = item.age_seconds
    if age is None:
        return "gerade gefunden"
    if age < 60:
        return f"vor {age}s online"
    if age < 3600:
        return f"vor {age // 60} Min online"
    return f"vor {age // 3600} Std online"


def item_embed(item: Item, watch: Watch) -> discord.Embed:
    domain = domains.lookup(item.host)

    embed = discord.Embed(
        title=item.title[:250],
        url=item.url,
        color=VINTED_TEAL,
        timestamp=dt.datetime.now(dt.timezone.utc),
    )
    embed.add_field(name="Preis", value=item.price_label(), inline=True)
    if item.size:
        embed.add_field(name="Größe", value=item.size, inline=True)
    if item.brand:
        embed.add_field(name="Marke", value=item.brand, inline=True)
    if item.condition:
        embed.add_field(name="Zustand", value=item.condition, inline=True)
    if item.seller:
        seller = f"[{item.seller}]({item.seller_url})" if item.seller_url else item.seller
        embed.add_field(name="Verkäufer", value=seller, inline=True)
    embed.add_field(name="Online", value=_age_label(item), inline=True)

    if item.photo_url:
        embed.set_image(url=item.photo_url)

    embed.set_footer(text=f"{domain.flag} {item.host} · Watch „{watch.name}“ (#{watch.id})")
    return embed


def item_view(item: Item) -> discord.ui.View:
    """Zwei Link-Buttons: Artikelseite und Direkteinstieg in den Kauf."""
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(label="Artikel öffnen", style=discord.ButtonStyle.link, url=item.url)
    )
    view.add_item(
        discord.ui.Button(label="Sofort kaufen", style=discord.ButtonStyle.link, url=item.buy_url)
    )
    return view


def watch_list_embed(watches: list[Watch], running: set[int]) -> discord.Embed:
    embed = discord.Embed(
        title="Aktive Suchen",
        color=VINTED_TEAL,
        description=None if watches else "Noch keine Suche angelegt — `/watch add`.",
    )
    for watch in watches[:25]:
        domain = domains.lookup(watch.host)
        if not watch.enabled:
            state = "⏸️ pausiert"
        elif watch.last_error:
            state = "⚠️ Fehler"
        elif watch.id in running:
            state = "🟢 läuft"
        else:
            state = "⚪ startet"

        lines = [
            f"{state} · alle {watch.interval}s · <#{watch.channel_id}>",
            f"{domain.flag} {watch.query.describe()}",
            f"{watch.hits} Treffer bisher",
        ]
        if watch.last_error:
            lines.append(f"Letzter Fehler: {watch.last_error[:120]}")
        embed.add_field(name=f"#{watch.id} · {watch.name}", value="\n".join(lines), inline=False)

    if len(watches) > 25:
        embed.set_footer(text=f"… und {len(watches) - 25} weitere.")
    return embed


def watch_created_embed(watch: Watch, sample: int) -> discord.Embed:
    domain = domains.lookup(watch.host)
    embed = discord.Embed(
        title=f"Suche #{watch.id} „{watch.name}“ läuft",
        color=OK_GREEN,
        description=(
            f"{domain.flag} **{watch.host}** · {watch.query.describe()}\n"
            f"Prüfung alle **{watch.interval}s**, Alerts in <#{watch.channel_id}>."
        ),
    )
    embed.add_field(
        name="Ausgangsbestand",
        value=(
            f"{sample} aktuelle Artikel wurden erfasst und **nicht** gemeldet. "
            "Ab jetzt kommt nur noch, was neu eingestellt wird."
        ),
        inline=False,
    )
    embed.add_field(name="Such-URL", value=f"[auf Vinted öffnen]({watch.source_url})", inline=False)
    return embed


def status_embed(
    *,
    watches: list[Watch],
    running: set[int],
    sessions: dict[str, str],
    started_at: dt.datetime,
) -> discord.Embed:
    active = [w for w in watches if w.enabled]
    failing = [w for w in active if w.last_error]

    color = WARN_ORANGE if failing else OK_GREEN
    embed = discord.Embed(title="Sniper-Status", color=color)
    embed.add_field(
        name="Suchen",
        value=(
            f"{len(active)} aktiv · {len(watches) - len(active)} pausiert\n"
            f"{len(running)} Tasks laufen · {len(failing)} mit Fehler"
        ),
        inline=False,
    )
    embed.add_field(
        name="Treffer gesamt",
        value=str(sum(w.hits for w in watches)),
        inline=True,
    )
    embed.add_field(
        name="Läuft seit",
        value=discord.utils.format_dt(started_at, style="R"),
        inline=True,
    )
    if sessions:
        embed.add_field(
            name="Vinted-Sessions",
            value="\n".join(f"`{host}` — {state}" for host, state in sessions.items()),
            inline=False,
        )
    return embed
