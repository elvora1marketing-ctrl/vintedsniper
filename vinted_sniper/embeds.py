"""Aufbereitung von Listings und Watches als Discord-Embeds."""

from __future__ import annotations

import datetime as dt
from typing import Any

import discord

from . import deals, traffic
from .db import Watch
from .vinted import domains
from .vinted.models import Item

VINTED_TEAL = 0x09B1BA
WARN_ORANGE = 0xE67E22
OK_GREEN = 0x2ECC71
# Deutlich unter Marktpreis. Ein eigener Farbton, damit im Nachrichtenstrom
# sofort auffällt, wo sich das Hinschauen lohnt.
DEAL_GREEN = 0x21A366
# Kaufbar, aber erst nachfragen.
DEAL_AMBER = 0xE0A400


def _age_label(item: Item) -> str:
    age = item.age_seconds
    if age is None:
        return "gerade gefunden"
    if age < 60:
        return f"vor {age}s online"
    if age < 3600:
        return f"vor {age // 60} Min online"
    return f"vor {age // 3600} Std online"


def item_embed(item: Item, watch: Watch, *, include_links: bool = False) -> discord.Embed:
    """Ein Listing als Embed.

    `include_links` hängt die Links als Feld an. Das braucht der Webhook-Weg:
    ein per URL angelegter Webhook gehört keiner Anwendung und darf deshalb
    keine Buttons mitschicken — ohne das Feld käme der Alert ohne Kauflink an.

    `item.price_note` ordnet den Preis ein („38 % unter Median"). Beim Snipen
    zählt genau das: ob sich der Klick lohnt, entscheidet sich in Sekunden.
    """
    domain = domains.lookup(item.host)
    schnaeppchen = bool(item.price_note and "unter**" in item.price_note)
    urteil = item.verdict

    if urteil is not None:
        gruen = urteil.grade == deals.GREEN
        farbe = DEAL_GREEN if gruen else DEAL_AMBER
        titel = f"{'🟢' if gruen else '🟡'} {item.title}"
    else:
        farbe = DEAL_GREEN if schnaeppchen else VINTED_TEAL
        titel = item.title

    embed = discord.Embed(
        title=titel[:250],
        # Eigene Farbe für Funde unter Marktpreis: im Nachrichtenstrom sieht
        # man das, bevor man liest.
        color=farbe,
        url=item.url,
        timestamp=dt.datetime.now(dt.timezone.utc),
    )
    if urteil is not None:
        embed.description = urteil.headline()
        if urteil.notes:
            embed.description += "\n⚠️ " + " · ".join(urteil.notes)
        rechnung = urteil.breakdown()
        if rechnung:
            # Die Rechnung steht im Alert, nicht nur das Ergebnis: eine Zahl
            # ohne Herleitung kann man nicht prüfen, und geprüft werden muss.
            embed.add_field(name="Rechnung", value=rechnung, inline=False)
    preis = item.price_label()
    if item.price_note:
        preis = f"{preis}\n{item.price_note}"
    embed.add_field(name="Preis", value=preis, inline=True)
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

    if urteil is not None:
        # Die Prüfliste gehört in den Alert, nicht in eine Doku: in den 90
        # Sekunden, die für die Entscheidung bleiben, macht niemand einen
        # zweiten Tab auf.
        embed.add_field(
            name="In 90 Sekunden prüfen",
            value="\n".join(f"☐ {punkt}" for punkt in deals.CHECKLIST),
            inline=False,
        )
        embed.add_field(
            name="Wirklich verkaufte Vergleichsartikel",
            value=f"[bei eBay nachsehen]({deals.ebay_sold_url(item)})",
            inline=False,
        )
        if urteil.grade == deals.YELLOW:
            # Gelb heißt „erst nachfragen" — dann gehört die Nachricht dazu,
            # fertig zum Kopieren.
            embed.add_field(
                name="Nachricht an den Verkäufer",
                value=f"```\n{deals.SELLER_QUESTION}\n```",
                inline=False,
            )

    if include_links:
        embed.add_field(
            name="Links",
            value=f"[Artikel öffnen]({item.url}) · [Sofort kaufen]({item.buy_url})",
            inline=False,
        )

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

        ziel = (
            "per Webhook (aus `searches.toml`)"
            if watch.origin == "file"
            else f"<#{watch.channel_id}>"
        )
        lines = [
            f"{state} · alle {watch.interval}s · {ziel}",
            f"{domain.flag} {watch.query.describe()}",
            f"{watch.hits} Treffer bisher",
        ]
        if watch.last_error:
            lines.append(f"Letzter Fehler: {watch.last_error[:120]}")
        embed.add_field(name=f"#{watch.id} · {watch.name}", value="\n".join(lines), inline=False)

    if len(watches) > 25:
        embed.set_footer(text=f"… und {len(watches) - 25} weitere.")
    elif any(w.origin == "file" for w in watches):
        embed.set_footer(
            text="Suchen aus searches.toml lassen sich mit /watch import "
            "hierher holen und dann normal verwalten."
        )
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
    meter: traffic.Meter | None = None,
    window: Any = None,
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
    if meter is not None and meter.requests:
        # Beim Proxy zahlt man nach Volumen. Die Hochrechnung ist die Zahl,
        # die man vor dem Nachkaufen sehen will.
        aktiv = [w for w in watches if w.enabled]
        takt = (
            sum(w.interval for w in aktiv) / len(aktiv) if aktiv else 0.0
        )
        anteil = window.daily_fraction if window is not None else 1.0
        prognose = meter.forecast(len(aktiv), takt, fraction=anteil)
        text = meter.summary()
        if prognose:
            text += (
                f"\n**Hochgerechnet: {traffic.human(prognose)} in 30 Tagen** "
                f"bei {len(aktiv)} Suchen alle {takt:.0f}s"
                + (f" und {anteil * 24:.0f} h am Tag" if anteil < 1.0 else "")
            )
        embed.add_field(name="Proxy-Volumen", value=text, inline=False)
    if sessions:
        embed.add_field(
            name="Vinted-Sessions",
            value="\n".join(f"`{host}` — {state}" for host, state in sessions.items()),
            inline=False,
        )
    return embed
