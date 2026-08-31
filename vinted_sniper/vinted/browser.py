"""Abfragen über einen echten Chromium statt über HTTP.

Der Cookie-Export aus einem Browser reicht gegen moderne Bot-Erkennung nicht:
Datadome prüft nicht nur, ob man die richtigen Cookies hat, sondern auch, wer
sie benutzt. Cookies aus dem Browser in einen HTTP-Client zu tragen fällt dabei
auf.

Hier läuft die Anfrage deshalb **im** Browser: `fetch()` wird im Kontext der
geladenen Vinted-Seite ausgeführt. Damit stimmen TLS-Fingerprint, HTTP/2-Rahmen,
Cookies, Header-Reihenfolge und JavaScript-Umgebung alle zueinander — weil sie
tatsächlich von einem Browser kommen.

Der Preis ist Geschwindigkeit und Arbeitsspeicher, deshalb ist das der zweite
Weg: erst HTTP, und erst wenn Vinted dort blockt, wird umgeschaltet.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

log = logging.getLogger(__name__)

# Grobe Zuordnung Domain → Zeitzone. Eine Browser-Zeitzone, die nicht zur
# Sprache und zur IP passt, ist ein billiges Erkennungsmerkmal.
_TIMEZONES = {
    "www.vinted.de": "Europe/Berlin",
    "www.vinted.at": "Europe/Vienna",
    "www.vinted.fr": "Europe/Paris",
    "www.vinted.be": "Europe/Brussels",
    "www.vinted.lu": "Europe/Luxembourg",
    "www.vinted.nl": "Europe/Amsterdam",
    "www.vinted.es": "Europe/Madrid",
    "www.vinted.it": "Europe/Rome",
    "www.vinted.pt": "Europe/Lisbon",
    "www.vinted.ie": "Europe/Dublin",
    "www.vinted.gr": "Europe/Athens",
    "www.vinted.fi": "Europe/Helsinki",
    "www.vinted.sk": "Europe/Bratislava",
    "www.vinted.lt": "Europe/Vilnius",
    "www.vinted.co.uk": "Europe/London",
    "www.vinted.pl": "Europe/Warsaw",
    "www.vinted.cz": "Europe/Prague",
    "www.vinted.se": "Europe/Stockholm",
    "www.vinted.dk": "Europe/Copenhagen",
    "www.vinted.ro": "Europe/Bucharest",
    "www.vinted.hu": "Europe/Budapest",
    "www.vinted.com": "America/New_York",
}

# Playwright hinterlässt Spuren, die jede Bot-Erkennung als Erstes abfragt.
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});
window.chrome = window.chrome || { runtime: {} };
const query = window.navigator.permissions && window.navigator.permissions.query;
if (query) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : query(parameters)
    );
}
"""

# Im Browser ausgeführte Abfrage. `credentials: 'include'` sorgt dafür, dass die
# Session-Cookies der geladenen Seite mitgehen.
_FETCH_SCRIPT = """
async (url) => {
    try {
        const response = await fetch(url, {
            headers: { 'Accept': 'application/json, text/plain, */*' },
            credentials: 'include',
        });
        return { status: response.status, body: await response.text() };
    } catch (error) {
        return { status: 0, body: String(error) };
    }
}
"""


class BrowserUnavailable(RuntimeError):
    """Playwright fehlt oder der Browser lässt sich nicht starten."""


def playwright_proxy(proxy_url: str) -> dict[str, str]:
    """`http://user:pass@host:port` in Playwrights Proxy-Format übersetzen.

    Playwright erwartet Zugangsdaten in eigenen Feldern und ignoriert sie, wenn
    sie in der Server-URL stecken. Bei fast allen Residential-Proxies gehören
    Zugangsdaten dazu — ohne diese Aufteilung schlägt jede Anfrage fehl.
    """
    parts = urlsplit(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    server = f"{parts.scheme}://{parts.hostname or ''}"
    if parts.port:
        server += f":{parts.port}"

    config = {"server": server}
    if parts.username:
        config["username"] = unquote(parts.username)
    if parts.password:
        config["password"] = unquote(parts.password)
    return config


class BrowserFetcher:
    """Hält einen Chromium offen und holt darin die Katalog-Antworten."""

    def __init__(
        self,
        host: str,
        *,
        language: str,
        proxy: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.host = host
        self.language = language
        self.proxy = proxy
        self.timeout_ms = int(timeout * 1000)

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def running(self) -> bool:
        return self._page is not None

    async def start(self) -> None:
        """Browser hochfahren und die Katalogseite laden.

        Die Seite muss geladen sein, bevor `fetch_json` läuft: `fetch()` wird
        im Kontext dieser Seite ausgeführt, also muss sie zur selben Domain
        gehören — sonst blockt die Same-Origin-Policy die Anfrage.
        """
        await self.close()

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable("Playwright ist nicht installiert.") from exc

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                proxy=playwright_proxy(self.proxy) if self.proxy else None,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            # Der voreingestellte User-Agent enthält „HeadlessChrome" und
            # verrät den Bot sofort. Die Version kommt aus dem laufenden
            # Browser, damit sie nach einem Playwright-Update noch stimmt.
            major = (self._browser.version or "130.0.0.0").split(".")[0]
            user_agent = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, "
                f"like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
            )

            self._context = await self._browser.new_context(
                user_agent=user_agent,
                locale=self.language,
                timezone_id=_TIMEZONES.get(self.host, "Europe/Berlin"),
                viewport={"width": 1440, "height": 900},
                java_script_enabled=True,
            )
            await self._context.add_init_script(_STEALTH_SCRIPT)

            # Bilder, Videos und Schriften sind für die Katalogabfrage wertlos,
            # machen aber den Großteil des Datenvolumens aus — auf einer
            # Vinted-Katalogseite mehrere Megabyte. Beim Proxy zahlt man nach
            # Volumen, nicht nach Abfragen. Javascript und die API-Aufrufe
            # bleiben unangetastet: daran hängt die Antibot-Prüfung.
            await self._context.route("**/*", self._nur_notwendiges)

            self._page = await self._context.new_page()
            response = await self._page.goto(
                f"https://{self.host}/catalog",
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            status = response.status if response is not None else 0
            if status in (403, 429):
                raise BrowserUnavailable(
                    f"Auch der Browser bekommt HTTP {status} von {self.host}. "
                    "Das spricht für eine Sperre der Server-IP — dagegen hilft "
                    "nur ein Proxy (PROXIES in der .env)."
                )
            # Challenges brauchen einen Moment, bis sie ihre Cookies setzen.
            await self._page.wait_for_timeout(4000)
            log.info("[%s] Browser-Sitzung steht (HTTP %s).", self.host, status)
        except Exception:
            await self.close()
            raise

    async def fetch_json(self, path: str, params: dict[str, str]) -> tuple[int, str]:
        """Katalog-Abfrage im Browser ausführen. Gibt (Status, Rohtext) zurück."""
        if self._page is None:
            await self.start()
        assert self._page is not None

        url = f"https://{self.host}{path}?{urlencode(params)}"
        result = await self._page.evaluate(_FETCH_SCRIPT, url)
        return int(result.get("status", 0)), str(result.get("body", ""))

    # Alles hiervon wird verworfen, bevor es über die Leitung geht.
    BLOCKIERT = {"image", "media", "font"}

    async def _nur_notwendiges(self, route, request) -> None:
        """Schwere Nebensachen abbrechen, den Rest durchlassen."""
        try:
            if request.resource_type in self.BLOCKIERT:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            # Eine abgebrochene Navigation macht die Route ungültig. Das ist
            # kein Fehler, den man behandeln müsste — nur einer, der den
            # Browser nicht mitreißen darf.
            pass

    async def close(self) -> None:
        for attribute in ("_page", "_context", "_browser"):
            handle = getattr(self, attribute, None)
            if handle is not None:
                try:
                    await handle.close()
                except Exception:
                    pass
                setattr(self, attribute, None)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
