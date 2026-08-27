"""Zugriff auf Vinteds Katalog-Endpunkt."""

from __future__ import annotations

import logging

from ..config import Settings
from .models import Item
from .session import SessionPool
from .urls import SearchQuery

log = logging.getLogger(__name__)

CATALOG_PATH = "/api/v2/catalog/items"


class VintedClient:
    def __init__(self, settings: Settings, pool: SessionPool | None = None) -> None:
        self.settings = settings
        self.pool = pool or SessionPool(settings)

    async def search(self, query: SearchQuery, *, per_page: int | None = None) -> list[Item]:
        """Erste Ergebnisseite einer Suche, sortiert nach „neueste zuerst"."""
        session = await self.pool.get(query.host)
        params = query.api_params(page=1, per_page=per_page or self.settings.per_page)
        payload = await session.get_json(CATALOG_PATH, params)

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            log.warning("[%s] Unerwartete API-Antwort ohne `items`.", query.host)
            return []

        currency = query.domain.currency
        items: list[Item] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = Item.parse(raw, query.host, currency)
            if item is not None:
                items.append(item)
        return items

    async def close(self) -> None:
        await self.pool.close()
