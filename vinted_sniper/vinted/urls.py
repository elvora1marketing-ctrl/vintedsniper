"""Parser: Vinted-Such-URL aus dem Browser → Parameter für die Katalog-API.

Die Filter der Website landen als Query-String in der URL (`?search_text=...&
brand_ids[]=53&price_to=30`). Genau diese Parameter frisst auch der interne
Endpunkt `/api/v2/catalog/items`, nur ohne `[]`-Suffix und mit
komma-separierten Listen. Wir müssen die Filter also nicht nachbauen — der
Nutzer klickt sie auf vinted.de zusammen und fügt die URL ein.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlparse

from . import domains

# Query-Parameter, die die API kennt. Alles andere (Tracking, UI-State) fliegt
# raus, damit wir keinen Müll mitschleppen.
_LIST_PARAMS = {
    "catalog_ids",
    "brand_ids",
    "size_ids",
    "status_ids",
    "color_ids",
    "material_ids",
    "country_ids",
    "city_ids",
    "video_game_rating_ids",
}

_SCALAR_PARAMS = {
    "search_text",
    "price_from",
    "price_to",
    "currency",
    "is_for_swap",
}

# Die Website benutzt teils andere Namen als die API.
_ALIASES = {
    "catalog": "catalog_ids",
    "catalog[]": "catalog_ids",
    "brand": "brand_ids",
    "size": "size_ids",
    "status": "status_ids",
    "color": "color_ids",
    "material": "material_ids",
    "country": "country_ids",
    "city": "city_ids",
    "price_min": "price_from",
    "price_max": "price_to",
    "q": "search_text",
}


class InvalidSearchURL(ValueError):
    """Die eingefügte URL ist keine brauchbare Vinted-Suche."""


@dataclass
class SearchQuery:
    host: str
    lists: dict[str, list[str]] = field(default_factory=dict)
    scalars: dict[str, str] = field(default_factory=dict)

    @property
    def domain(self) -> domains.Domain:
        return domains.lookup(self.host)

    def api_params(self, *, page: int, per_page: int) -> dict[str, str]:
        params: dict[str, str] = {
            "page": str(page),
            "per_page": str(per_page),
            # Für einen Sniper ist jede andere Sortierung nutzlos — wir wollen
            # immer den frischesten Kram oben, egal was in der URL stand.
            "order": "newest_first",
        }
        for key, values in self.lists.items():
            if values:
                params[key] = ",".join(values)
        params.update(self.scalars)
        params.setdefault("currency", self.domain.currency)
        return params

    def web_url(self) -> str:
        """Menschenlesbare Such-URL zurückbauen (für Embeds/`/watch list`)."""
        parts: list[str] = []
        for key, value in self.scalars.items():
            parts.append(f"{key}={quote(str(value), safe='')}")
        for key, values in self.lists.items():
            for value in values:
                parts.append(f"{key}[]={quote(str(value), safe='')}")
        parts.append("order=newest_first")
        return f"https://{self.host}/catalog?" + "&".join(parts)

    def describe(self) -> str:
        """Kurzbeschreibung der Filter für Discord."""
        bits: list[str] = []
        text = self.scalars.get("search_text")
        if text:
            bits.append(f'"{text}"')
        low, high = self.scalars.get("price_from"), self.scalars.get("price_to")
        currency = self.scalars.get("currency", self.domain.currency)
        if low and high:
            bits.append(f"{low}–{high} {currency}")
        elif high:
            bits.append(f"bis {high} {currency}")
        elif low:
            bits.append(f"ab {low} {currency}")
        for key, label in (
            ("catalog_ids", "Kategorien"),
            ("brand_ids", "Marken"),
            ("size_ids", "Größen"),
            ("status_ids", "Zustand"),
            ("color_ids", "Farben"),
        ):
            values = self.lists.get(key)
            if values:
                bits.append(f"{len(values)} {label}")
        return " · ".join(bits) if bits else "alle Artikel"


def _catalog_id_from_path(path: str) -> str | None:
    """`/catalog/1904-t-shirts` → `1904`."""
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "catalog":
        return None
    head = segments[1].split("-", 1)[0]
    return head if head.isdigit() else None


def parse_search_url(raw: str) -> SearchQuery:
    """Vinted-Such-URL in eine `SearchQuery` übersetzen.

    Wirft `InvalidSearchURL`, wenn die URL nicht zu Vinted gehört oder keine
    Katalogsuche ist.
    """
    raw = raw.strip().strip("<>")
    if not raw:
        raise InvalidSearchURL("Leere URL.")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    if not parsed.netloc:
        raise InvalidSearchURL("Das sieht nicht nach einer URL aus.")
    if not domains.is_vinted_host(parsed.netloc):
        raise InvalidSearchURL(
            f"`{parsed.netloc}` ist keine Vinted-Domain. Erwartet wird z. B. "
            "`www.vinted.de`."
        )

    host = domains.normalize_host(parsed.netloc)
    query = SearchQuery(host=host)

    catalog_from_path = _catalog_id_from_path(parsed.path)
    if catalog_from_path:
        query.lists.setdefault("catalog_ids", []).append(catalog_from_path)

    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        value = value.strip()
        if not value:
            continue
        name = _ALIASES.get(key, key.removesuffix("[]"))
        name = _ALIASES.get(name, name)
        if name in _LIST_PARAMS:
            bucket = query.lists.setdefault(name, [])
            # Die Website schreibt Listen mal als `brand_ids[]=1&brand_ids[]=2`,
            # mal als `brand_ids=1,2`.
            for part in value.split(","):
                part = part.strip()
                if part and part not in bucket:
                    bucket.append(part)
        elif name in _SCALAR_PARAMS:
            query.scalars[name] = value

    if not query.lists and not query.scalars:
        raise InvalidSearchURL(
            "In der URL stecken keine Filter. Stelle die Suche auf Vinted ein "
            "(Suchbegriff, Marke, Preis …) und kopiere dann die Adresszeile."
        )

    return query
