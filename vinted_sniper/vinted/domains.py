"""Bekannte Vinted-Länderdomains samt Währung und Sprache.

Die Liste ist nur für Defaults (Währungsanzeige, Accept-Language) da — unbekannte
`vinted.<tld>`-Domains funktionieren ebenfalls, sie fallen dann auf EUR/en zurück.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `www.vinted.de`, `www.vinted.xyz` — aber ausdrücklich nichts mit weiteren
# Labels dahinter.
_SINGLE_TLD = re.compile(r"www\.vinted\.[a-z]{2,10}")


@dataclass(frozen=True)
class Domain:
    host: str
    currency: str
    language: str
    flag: str


_DOMAINS: tuple[Domain, ...] = (
    Domain("www.vinted.de", "EUR", "de-DE", "🇩🇪"),
    Domain("www.vinted.at", "EUR", "de-AT", "🇦🇹"),
    Domain("www.vinted.fr", "EUR", "fr-FR", "🇫🇷"),
    Domain("www.vinted.be", "EUR", "fr-BE", "🇧🇪"),
    Domain("www.vinted.lu", "EUR", "fr-LU", "🇱🇺"),
    Domain("www.vinted.nl", "EUR", "nl-NL", "🇳🇱"),
    Domain("www.vinted.es", "EUR", "es-ES", "🇪🇸"),
    Domain("www.vinted.it", "EUR", "it-IT", "🇮🇹"),
    Domain("www.vinted.pt", "EUR", "pt-PT", "🇵🇹"),
    Domain("www.vinted.ie", "EUR", "en-IE", "🇮🇪"),
    Domain("www.vinted.gr", "EUR", "el-GR", "🇬🇷"),
    Domain("www.vinted.fi", "EUR", "fi-FI", "🇫🇮"),
    Domain("www.vinted.sk", "EUR", "sk-SK", "🇸🇰"),
    Domain("www.vinted.lt", "EUR", "lt-LT", "🇱🇹"),
    Domain("www.vinted.co.uk", "GBP", "en-GB", "🇬🇧"),
    Domain("www.vinted.pl", "PLN", "pl-PL", "🇵🇱"),
    Domain("www.vinted.cz", "CZK", "cs-CZ", "🇨🇿"),
    Domain("www.vinted.se", "SEK", "sv-SE", "🇸🇪"),
    Domain("www.vinted.dk", "DKK", "da-DK", "🇩🇰"),
    Domain("www.vinted.ro", "RON", "ro-RO", "🇷🇴"),
    Domain("www.vinted.hu", "HUF", "hu-HU", "🇭🇺"),
    Domain("www.vinted.com", "USD", "en-US", "🇺🇸"),
)

_BY_HOST = {d.host: d for d in _DOMAINS}

FALLBACK = Domain("www.vinted.de", "EUR", "en-US", "🌍")


def normalize_host(host: str) -> str:
    """`vinted.de` → `www.vinted.de`."""
    host = host.strip().lower().removeprefix("http://").removeprefix("https://")
    host = host.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    if not host:
        return FALLBACK.host
    if host.startswith("vinted."):
        host = "www." + host
    return host


def lookup(host: str) -> Domain:
    normalized = normalize_host(host)
    known = _BY_HOST.get(normalized)
    if known is not None:
        return known
    # Unbekannte, aber plausible Vinted-Domain: mit EUR-Defaults weiterlaufen.
    return Domain(normalized, FALLBACK.currency, FALLBACK.language, FALLBACK.flag)


def is_vinted_host(host: str) -> bool:
    """Nur echte Vinted-Hosts durchlassen.

    Ein reines Substring-Match („enthält `.vinted.`") würde
    `www.vinted.de.angreifer.com` akzeptieren und den Bot dazu bringen, seine
    Requests an einen fremden Server zu schicken. Deshalb: entweder exakt eine
    bekannte Domain, oder exakt `www.vinted.<tld>` mit genau einem TLD-Label.
    """
    normalized = normalize_host(host)
    if normalized in _BY_HOST:
        return True
    return _SINGLE_TLD.fullmatch(normalized) is not None


def known_hosts() -> list[str]:
    return [d.host for d in _DOMAINS]


# Kürzel, die niemand als TLD tippt, aber jeder als Land meint.
_ALIASES = {
    "uk": "www.vinted.co.uk",
    "gb": "www.vinted.co.uk",
    "en": "www.vinted.co.uk",
    "us": "www.vinted.com",
    "com": "www.vinted.com",
}


def resolve(text: str) -> Domain | None:
    """Ein Länderkürzel oder eine Domain zu einem bekannten Ziel auflösen.

    Nimmt `fr`, `.fr`, `vinted.fr`, `www.vinted.fr` und `uk` gleichermaßen.
    Gibt `None` zurück, wenn nichts Bekanntes gemeint sein kann — Tippfehler
    sollen nicht stillschweigend zu einer erfundenen Domain führen.
    """
    roh = text.strip().lower().lstrip(".")
    if not roh:
        return None
    if roh in _ALIASES:
        return _BY_HOST[_ALIASES[roh]]
    if "vinted." in roh:
        return _BY_HOST.get(normalize_host(roh))
    return _BY_HOST.get(f"www.vinted.{roh}")


def parse_list(text: str) -> tuple[list[Domain], list[str]]:
    """Eine Aufzählung wie `fr, nl, it` einlesen.

    Liefert die erkannten Domains (ohne Dubletten, in Eingabereihenfolge) und
    die Angaben, mit denen nichts anzufangen war.
    """
    gefunden: list[Domain] = []
    unbekannt: list[str] = []
    for teil in text.replace(";", ",").replace(" ", ",").split(","):
        if not teil.strip():
            continue
        domain = resolve(teil)
        if domain is None:
            unbekannt.append(teil.strip())
        elif domain not in gefunden:
            gefunden.append(domain)
    return gefunden, unbekannt
