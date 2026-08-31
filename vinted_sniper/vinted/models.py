"""Datenmodell für ein Vinted-Listing."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


def _money(raw: Any) -> tuple[float | None, str | None]:
    """Preisfeld der API lesen — mal `{"amount": "12.0", ...}`, mal Zahl."""
    if isinstance(raw, dict):
        amount = raw.get("amount")
        currency = raw.get("currency_code")
        try:
            return (float(amount), currency) if amount is not None else (None, currency)
        except (TypeError, ValueError):
            return None, currency
    try:
        return float(raw), None
    except (TypeError, ValueError):
        return None, None


@dataclass(frozen=True)
class Item:
    id: str
    host: str
    title: str
    url: str
    price: float | None
    total_price: float | None
    currency: str
    brand: str | None
    size: str | None
    condition: str | None
    photo_url: str | None
    seller: str | None
    seller_url: str | None
    favourites: int
    views: int
    posted_ts: int | None
    # Einordnung des Preises („38 % unter Median"). Wird erst beim Melden
    # gesetzt — die Vergleichsbasis kennt nur der Monitor, nicht der Parser.
    price_note: str | None = None
    # Urteil des Kaufprofils (`deals.Verdict`), ebenfalls erst beim Melden.
    # Bewusst untypisiert: `deals` liest dieses Modul, andersherum entstünde
    # ein Importkreis.
    verdict: Any = None

    @property
    def buy_url(self) -> str:
        """Direktlink in den Kaufabschluss — spart beim Snipen zwei Klicks."""
        return (
            f"https://{self.host}/transaction/buy/new"
            f"?source_screen=item&transaction%5Bitem_id%5D={quote(self.id)}"
        )

    @property
    def age_seconds(self) -> int | None:
        if self.posted_ts is None:
            return None
        return max(0, int(time.time()) - self.posted_ts)

    def price_label(self) -> str:
        if self.price is None:
            return "Preis unbekannt"
        label = f"{self.price:.2f} {self.currency}".replace(".", ",", 1)
        if self.total_price is not None and self.total_price > self.price:
            total = f"{self.total_price:.2f}".replace(".", ",", 1)
            label += f"  (inkl. Schutz: {total} {self.currency})"
        return label

    @classmethod
    def parse(cls, raw: dict[str, Any], host: str, fallback_currency: str) -> "Item | None":
        item_id = raw.get("id")
        if item_id is None:
            return None

        price, currency = _money(raw.get("price"))
        total_price, total_currency = _money(raw.get("total_item_price"))

        photo = raw.get("photo") or {}
        high_res = photo.get("high_resolution") or {}
        # Vinted liefert kein verlässliches `created_at`; der Zeitstempel des
        # hochgeladenen Fotos ist praktisch identisch mit dem Einstellzeitpunkt.
        posted_ts = high_res.get("timestamp") or raw.get("created_at_ts")
        if isinstance(posted_ts, str) and posted_ts.isdigit():
            posted_ts = int(posted_ts)
        if not isinstance(posted_ts, int):
            posted_ts = None

        user = raw.get("user") or {}
        url = raw.get("url") or f"https://{host}/items/{item_id}"

        return cls(
            id=str(item_id),
            host=host,
            title=(raw.get("title") or "Ohne Titel").strip(),
            url=url,
            price=price,
            total_price=total_price,
            currency=currency or total_currency or fallback_currency,
            brand=(raw.get("brand_title") or None),
            size=(raw.get("size_title") or None),
            condition=(raw.get("status") or None),
            photo_url=(high_res.get("url") or photo.get("url") or photo.get("full_size_url")),
            seller=(user.get("login") or None),
            seller_url=(user.get("profile_url") or None),
            favourites=int(raw.get("favourite_count") or 0),
            views=int(raw.get("view_count") or 0),
            posted_ts=posted_ts,
        )
