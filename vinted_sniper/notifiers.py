"""Alert-Zustellung per Discord-Webhook.

Ein Webhook ist der kürzeste Weg in einen Channel: kein Bot-Token, keine
Einladung, keine Rechtevergabe. Dafür kann er weniger — er empfängt keine
Slash-Commands, und er darf keine Buttons mitschicken, weil ein per URL
angelegter Webhook keiner Anwendung gehört. Die Links wandern deshalb in ein
Feld des Embeds.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord

from . import embeds
from .db import Watch
from .vinted import domains
from .vinted.models import Item

log = logging.getLogger(__name__)

# Discord drosselt Webhooks pro Channel; mit dem Abstand bleiben wir darunter.
SEND_DELAY = 0.7
# Mehr Treffer in einem Durchlauf heißt fast immer: der Filter ist zu weit.
MAX_ALERTS_PER_ROUND = 10


class WebhookNotifier:
    """Verschickt Alerts über Discord-Webhooks — eine Session für alle Ziele."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._webhooks: dict[str, discord.Webhook] = {}
        self._send_lock = asyncio.Lock()

    async def _webhook_for(self, url: str) -> discord.Webhook:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        webhook = self._webhooks.get(url)
        if webhook is None:
            webhook = discord.Webhook.from_url(url, session=self._session)
            self._webhooks[url] = webhook
        return webhook

    async def send_items(self, watch: Watch, items: list[Item]) -> None:
        if not watch.webhook_url:
            log.error("Suche #%d hat kein Webhook-Ziel — Alerts verworfen.", watch.id)
            return

        webhook = await self._webhook_for(watch.webhook_url)
        overflow = max(0, len(items) - MAX_ALERTS_PER_ROUND)

        async with self._send_lock:
            for item in items[:MAX_ALERTS_PER_ROUND]:
                try:
                    await webhook.send(
                        embed=embeds.item_embed(item, watch, include_links=True),
                        username="Vinted Sniper",
                    )
                except discord.NotFound:
                    # Gelöschter Webhook heilt nicht von selbst — einmal deutlich
                    # sagen und die Suche nicht weiter ins Leere posten lassen.
                    log.error(
                        "Webhook für Suche #%d existiert nicht mehr. Neuen Webhook "
                        "anlegen und die URL eintragen.",
                        watch.id,
                    )
                    return
                except discord.HTTPException as exc:
                    log.error("Alert für Artikel %s fehlgeschlagen: %s", item.id, exc)
                await asyncio.sleep(SEND_DELAY)

            if overflow:
                await self._safe_send(
                    webhook,
                    content=(
                        f"… und **{overflow}** weitere Treffer für „{watch.name}“. "
                        "Die Suche ist vermutlich zu weit gefasst."
                    ),
                )

    async def send_trouble(self, watch: Watch, message: str) -> None:
        if not watch.webhook_url:
            return
        webhook = await self._webhook_for(watch.webhook_url)
        await self._safe_send(
            webhook,
            embed=discord.Embed(
                title=f"Suche „{watch.name}“ hakt",
                description=(
                    f"{message}\n\nEs wird weiter versucht, mit größeren Abständen "
                    "und neu aufgebauter Session."
                ),
                color=embeds.WARN_ORANGE,
            ),
        )

    async def send_recovered(self, watch: Watch, message: str) -> None:
        if not watch.webhook_url:
            return
        webhook = await self._webhook_for(watch.webhook_url)
        await self._safe_send(
            webhook,
            embed=discord.Embed(
                title=f"Suche „{watch.name}“ läuft wieder",
                description=message,
                color=embeds.OK_GREEN,
            ),
        )

    async def send_startup(self, url: str, watches: list[Watch]) -> None:
        """Einmalige Startmeldung — belegt sofort, dass der Webhook funktioniert."""
        webhook = await self._webhook_for(url)
        lines = [
            f"• **{watch.name}** — {watch.query.describe()} "
            f"({domain_label(watch)}, alle {watch.interval}s)"
            for watch in watches[:15]
        ]
        if len(watches) > 15:
            lines.append(f"• … und {len(watches) - 15} weitere")
        await self._safe_send(
            webhook,
            embed=discord.Embed(
                title="Vinted Sniper läuft",
                description=(
                    f"**{len(watches)}** Suche(n) aktiv:\n" + "\n".join(lines) + "\n\n"
                    "Der aktuelle Bestand wird jetzt eingelesen und **nicht** gemeldet. "
                    "Ab dem nächsten Durchlauf kommt nur noch, was neu eingestellt wird."
                ),
                color=embeds.OK_GREEN,
            ),
        )

    async def _safe_send(self, webhook: discord.Webhook, **kwargs: object) -> None:
        try:
            await webhook.send(username="Vinted Sniper", **kwargs)  # type: ignore[arg-type]
        except discord.HTTPException as exc:
            log.error("Webhook-Nachricht fehlgeschlagen: %s", exc)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._webhooks.clear()


def domain_label(watch: Watch) -> str:
    domain = domains.lookup(watch.host)
    return f"{domain.flag} {watch.host.removeprefix('www.')}"
