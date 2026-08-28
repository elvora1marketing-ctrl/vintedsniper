"""Diagnose: kommt man von hier aus überhaupt an Vinted heran?

`python -m vinted_sniper.check`

Prüft die direkte Verbindung und jeden in `PROXIES` eingetragenen Proxy einzeln,
und sagt am Ende, welcher Weg funktioniert. Damit lässt sich ein neu gekaufter
Proxy in Sekunden bewerten, statt den Sniper zu starten und Logdateien zu deuten.
"""

from __future__ import annotations

import asyncio
import sys

from curl_cffi.requests import AsyncSession

from .config import Settings
from .vinted import domains
from .vinted.browser import BrowserFetcher, BrowserUnavailable

# Zum Anzeigen der ausgehenden IP. Verrät, ob der Proxy überhaupt greift.
IP_SERVICE = "https://api.ipify.org"


def _mask(proxy: str) -> str:
    """Proxy-URL ohne Zugangsdaten — die Ausgabe landet oft in Chats."""
    if "@" in proxy:
        head, _, tail = proxy.rpartition("@")
        scheme = head.split("://", 1)[0] + "://" if "://" in head else ""
        return f"{scheme}<zugangsdaten>@{tail}"
    return proxy


async def _outgoing_ip(session: AsyncSession) -> str:
    try:
        response = await session.get(IP_SERVICE, timeout=15)
        return response.text.strip() or "unbekannt"
    except Exception:
        return "nicht ermittelbar"


async def _check_http(host: str, proxy: str | None, settings: Settings) -> bool:
    """Startseite über HTTP abrufen. Gibt zurück, ob der Weg frei ist."""
    domain = domains.lookup(host)
    proxies = {"http": proxy, "https": proxy} if proxy else None

    async with AsyncSession(
        impersonate=settings.impersonate,
        timeout=settings.request_timeout,
        proxies=proxies,
    ) as session:
        print(f"  Ausgehende IP:  {await _outgoing_ip(session)}")
        try:
            response = await session.get(
                f"https://{host}/",
                headers={"Accept-Language": domain.language},
                allow_redirects=True,
            )
        except Exception as exc:
            print(f"  {host}: Verbindung fehlgeschlagen — {exc}")
            return False

        status = response.status_code
        verdict = "frei" if status < 400 else "blockiert"
        print(f"  {host}: HTTP {status}  → {verdict}")
        return status < 400


async def _check_browser(host: str, proxy: str | None, settings: Settings) -> bool:
    """Gegenprobe mit echtem Chromium — der entscheidende Test."""
    if not settings.playwright_fallback:
        print("  Browser-Test übersprungen (PLAYWRIGHT_FALLBACK=false).")
        return False

    fetcher = BrowserFetcher(
        host,
        language=domains.lookup(host).language,
        proxy=proxy,
        timeout=max(settings.request_timeout, 45.0),
    )
    try:
        await fetcher.start()
    except BrowserUnavailable as exc:
        print(f"  Browser: {exc}")
        return False
    except Exception as exc:
        print(f"  Browser: Start fehlgeschlagen — {exc}")
        return False

    try:
        status, body = await fetcher.fetch_json(
            "/api/v2/catalog/items",
            {"page": "1", "per_page": "1", "order": "newest_first"},
        )
        if status == 200 and '"items"' in body:
            print("  Browser: HTTP 200, Katalog liefert Artikel  → funktioniert")
            return True
        print(f"  Browser: Katalog-Abfrage HTTP {status}  → blockiert")
        return False
    finally:
        await fetcher.close()


async def run(host: str) -> int:
    settings = Settings.load(require_target=False)

    print(f"\n=== Erreichbarkeit von {host} ===\n")
    working: list[str] = []

    print("Direkt (IP dieses Servers)")
    if await _check_http(host, None, settings):
        working.append("direkt")
    else:
        if await _check_browser(host, None, settings):
            working.append("direkt (nur Browser-Modus)")
    print()

    for index, proxy in enumerate(settings.proxies, start=1):
        print(f"Proxy #{index}: {_mask(proxy)}")
        if await _check_http(host, proxy, settings):
            working.append(f"Proxy #{index}")
        elif await _check_browser(host, proxy, settings):
            working.append(f"Proxy #{index} (nur Browser-Modus)")
        print()

    if not settings.proxies:
        print("In PROXIES ist nichts eingetragen — es wurde nur direkt geprüft.\n")

    print("--- Ergebnis ---")
    if working:
        print("Funktionierende Wege: " + ", ".join(working))
        print("Der Sniper kann laufen. Starten mit: docker compose up -d")
        return 0

    print(
        "Kein Weg kommt durch. Die IP steht auf Vinteds Sperrliste.\n"
        "Was hilft:\n"
        "  • Ein Residential- oder ISP-Proxy (keine Datacenter-IP!), am besten\n"
        "    aus dem Land der Domain — für vinted.de also eine deutsche IP.\n"
        "  • Oder den Sniper von einem Anschluss außerhalb des Rechenzentrums\n"
        "    betreiben."
    )
    return 1


def main() -> int:
    host = domains.normalize_host(sys.argv[1]) if len(sys.argv) > 1 else "www.vinted.de"
    try:
        return asyncio.run(run(host))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
