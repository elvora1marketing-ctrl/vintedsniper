"""Session-Handling gegen Vinted inklusive Antibot-Selbstheilung.

Vinted steht hinter Cloudflare und Datadome. Ein simpler `requests`-Aufruf
fliegt dort sofort raus, weil der TLS-Fingerprint (JA3) und die HTTP/2-Settings
nicht zu dem User-Agent passen, den man behauptet zu sein. Deshalb:

* **curl_cffi mit Impersonation** — die Verbindung sieht auf TLS- und
  HTTP/2-Ebene aus wie echtes Chrome, nicht nur im User-Agent-String.
* **Session-Bootstrap** — vor dem ersten API-Call wird die normale Startseite
  geladen, damit man die Cookies (`access_token_web`, `anon_id`, …) besitzt,
  die die API erwartet.
* **Automatischer Refresh** — bei 401 wird das Token über den regulären
  Web-Endpunkt erneuert statt eine neue Session aufzumachen.
* **Playwright-Fallback** — wenn der HTTP-Bootstrap an einer Challenge
  scheitert, holt ein echter Headless-Chromium die Cookies und übergibt sie an
  die HTTP-Session.
* **Proxy-Rotation und Backoff** — bei 403/429 wird die Ausgangs-IP gewechselt
  und exponentiell zurückgefahren, statt stumpf weiterzuhämmern.

Es gibt keinen Zustand, in dem der Sniper dauerhaft „tot" ist: jeder Blocker
löst einen Reparaturpfad aus.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from curl_cffi.requests import AsyncSession

from ..config import Settings
from . import domains

log = logging.getLogger(__name__)

# Vinted antwortet auf Challenges mit HTML statt JSON; diese Marker verraten
# uns, dass wir nicht am API-, sondern am Bot-Schutz hängen.
_CHALLENGE_MARKERS = (
    "datadome",
    "captcha-delivery",
    "cf-challenge",
    "just a moment",
    "checking your browser",
    "attention required",
)


class VintedError(RuntimeError):
    """Basisfehler für alles, was die Vinted-Anbindung wirft."""


class AuthExpired(VintedError):
    """Session-Token ist abgelaufen (HTTP 401)."""


class Blocked(VintedError):
    """Antibot hat zugeschlagen (403/429/Challenge-HTML)."""


class RateLimiter:
    """Simples Token-Bucket: höchstens N Requests pro Minute und Host."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / max(1, per_minute)
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_slot = now + self._interval


class VintedSession:
    """Eine wiederverwendbare, selbstheilende Session für genau einen Host."""

    MAX_ATTEMPTS = 4

    def __init__(self, host: str, settings: Settings) -> None:
        self.host = host
        self.domain = domains.lookup(host)
        self.settings = settings

        self._session: AsyncSession | None = None
        self._lock = asyncio.Lock()
        self._bootstrapped_at = 0.0
        self._proxy_index = 0
        self._consecutive_blocks = 0
        self._playwright_available = settings.playwright_fallback
        self._limiter = RateLimiter(settings.rate_limit_per_domain)

    # ------------------------------------------------------------------ Status

    @property
    def healthy(self) -> bool:
        return self._session is not None and self._consecutive_blocks == 0

    def status_line(self) -> str:
        if self._session is None:
            return "nicht initialisiert"
        if self._consecutive_blocks:
            return f"blockiert ({self._consecutive_blocks}× in Folge)"
        age = int(time.monotonic() - self._bootstrapped_at)
        return f"ok (Session {age}s alt)"

    # ------------------------------------------------------------- Netzwerk-Setup

    def _current_proxy(self) -> dict[str, str] | None:
        if not self.settings.proxies:
            return None
        proxy = self.settings.proxies[self._proxy_index % len(self.settings.proxies)]
        return {"http": proxy, "https": proxy}

    def _rotate_proxy(self) -> None:
        if len(self.settings.proxies) > 1:
            self._proxy_index += 1
            log.info(
                "[%s] Wechsle auf Proxy #%d",
                self.host,
                self._proxy_index % len(self.settings.proxies),
            )

    def _base_headers(self) -> dict[str, str]:
        # curl_cffi setzt bei aktiver Impersonation User-Agent und die
        # sec-ch-ua-*-Header selbst passend zum gewählten Browser. Wir ergänzen
        # nur, was vom Land abhängt.
        return {
            "Accept-Language": f"{self.domain.language},"
            f"{self.domain.language.split('-')[0]};q=0.9,en;q=0.8",
            "Referer": f"https://{self.host}/",
        }

    async def _new_session(self) -> AsyncSession:
        return AsyncSession(
            impersonate=self.settings.impersonate,
            timeout=self.settings.request_timeout,
            proxies=self._current_proxy(),
            verify=True,
        )

    # ---------------------------------------------------------------- Bootstrap

    async def _bootstrap(self, *, force_browser: bool = False) -> None:
        """Frische Session mit gültigen Cookies herstellen."""
        await self.close()
        session = await self._new_session()

        if not force_browser:
            try:
                response = await session.get(
                    f"https://{self.host}/",
                    headers=self._base_headers(),
                    allow_redirects=True,
                )
            except Exception as exc:  # Netzwerk, DNS, Proxy …
                await session.close()
                raise VintedError(f"Verbindung zu {self.host} fehlgeschlagen: {exc}") from exc

            body = response.text[:4000].lower()
            challenged = response.status_code in (403, 429) or any(
                marker in body for marker in _CHALLENGE_MARKERS
            )
            if not challenged and response.status_code < 400:
                self._session = session
                self._bootstrapped_at = time.monotonic()
                log.info("[%s] Session per HTTP aufgebaut.", self.host)
                return

            log.warning(
                "[%s] HTTP-Bootstrap blockiert (HTTP %s) — versuche Browser-Fallback.",
                self.host,
                response.status_code,
            )

        # HTTP-Weg blockiert: echten Browser die Challenge lösen lassen.
        cookies = await self._cookies_via_browser()
        if cookies is None:
            await session.close()
            raise Blocked(
                f"{self.host} verweigert den Zugriff und der Browser-Fallback "
                "steht nicht zur Verfügung."
            )
        for name, value in cookies.items():
            session.cookies.set(name, value, domain="." + self.host.removeprefix("www."))
        self._session = session
        self._bootstrapped_at = time.monotonic()
        log.info("[%s] Session per Headless-Browser aufgebaut.", self.host)

    async def _cookies_via_browser(self) -> dict[str, str] | None:
        """Cookies mit echtem Chromium holen (löst JS-Challenges mit)."""
        if not self._playwright_available:
            return None
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.warning("Playwright ist nicht installiert — Browser-Fallback deaktiviert.")
            self._playwright_available = False
            return None

        proxy_config = None
        if self.settings.proxies:
            proxy_config = {
                "server": self.settings.proxies[
                    self._proxy_index % len(self.settings.proxies)
                ]
            }

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    proxy=proxy_config,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                try:
                    context = await browser.new_context(
                        locale=self.domain.language,
                        viewport={"width": 1440, "height": 900},
                    )
                    page = await context.new_page()
                    await page.goto(
                        f"https://{self.host}/",
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                    # Challenges brauchen einen Moment, bis sie ihre Cookies setzen.
                    await page.wait_for_timeout(5_000)
                    jar = await context.cookies()
                finally:
                    await browser.close()
        except Exception as exc:
            log.error("[%s] Browser-Fallback fehlgeschlagen: %s", self.host, exc)
            return None

        cookies = {c["name"]: c["value"] for c in jar if c.get("value")}
        return cookies or None

    async def _refresh_token(self) -> bool:
        """Access-Token über den regulären Web-Endpunkt erneuern."""
        if self._session is None:
            return False
        try:
            response = await self._session.post(
                f"https://{self.host}/web/api/auth/refresh",
                headers={
                    **self._base_headers(),
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
                json={},
            )
        except Exception as exc:
            log.warning("[%s] Token-Refresh fehlgeschlagen: %s", self.host, exc)
            return False
        if response.status_code < 400:
            log.info("[%s] Access-Token erneuert.", self.host)
            return True
        log.info(
            "[%s] Token-Refresh abgelehnt (HTTP %s) — baue Session neu auf.",
            self.host,
            response.status_code,
        )
        return False

    async def _ensure_session(self) -> None:
        if self._session is None:
            await self._bootstrap()

    # ------------------------------------------------------------------ Requests

    async def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET auf einen API-Pfad mit allen Reparaturpfaden.

        Wirft `Blocked`, wenn auch nach Refresh, Proxy-Wechsel und
        Browser-Fallback nichts durchgeht.
        """
        async with self._lock:
            last_error: Exception | None = None

            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                await self._ensure_session()
                await self._limiter.acquire()
                assert self._session is not None

                try:
                    response = await self._session.get(
                        f"https://{self.host}{path}",
                        params=params,
                        headers={
                            **self._base_headers(),
                            "Accept": "application/json, text/plain, */*",
                            "Referer": f"https://{self.host}/catalog",
                        },
                    )
                except Exception as exc:
                    last_error = VintedError(f"Request fehlgeschlagen: {exc}")
                    log.warning("[%s] Versuch %d: %s", self.host, attempt, exc)
                    await self._backoff(attempt)
                    continue

                status = response.status_code

                if status == 401:
                    last_error = AuthExpired("Session abgelaufen.")
                    if not await self._refresh_token():
                        await self._bootstrap()
                    continue

                if status in (403, 429) or self._looks_like_challenge(response):
                    self._consecutive_blocks += 1
                    last_error = Blocked(f"HTTP {status} von {self.host}.")
                    log.warning(
                        "[%s] Versuch %d: Antibot (HTTP %s), Block #%d.",
                        self.host,
                        attempt,
                        status,
                        self._consecutive_blocks,
                    )
                    self._rotate_proxy()
                    await self._backoff(attempt)
                    # Nach dem zweiten Block direkt den Browser ranlassen — der
                    # reine HTTP-Bootstrap kommt an dieser Challenge nicht vorbei.
                    await self._bootstrap(force_browser=self._consecutive_blocks >= 2)
                    continue

                if status >= 500:
                    last_error = VintedError(f"Vinted-Serverfehler (HTTP {status}).")
                    await self._backoff(attempt)
                    continue

                if status >= 400:
                    raise VintedError(
                        f"Vinted antwortet mit HTTP {status}. Stimmen die "
                        "Filter in der Such-URL?"
                    )

                try:
                    payload = response.json()
                except Exception as exc:
                    last_error = VintedError(f"Antwort war kein JSON: {exc}")
                    await self._backoff(attempt)
                    continue

                self._consecutive_blocks = 0
                return payload

            raise last_error or VintedError("Unbekannter Fehler.")

    @staticmethod
    def _looks_like_challenge(response: Any) -> bool:
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            return False
        body = (response.text or "")[:2000].lower()
        return any(marker in body for marker in _CHALLENGE_MARKERS)

    async def _backoff(self, attempt: int) -> None:
        # Exponentiell mit Zufallsanteil: 2s, 4s, 8s, 16s (±30 %).
        base = min(2 ** attempt, 30)
        await asyncio.sleep(base * random.uniform(0.7, 1.3))

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None


class SessionPool:
    """Hält je Host genau eine Session — Domains teilen sich keine Cookies."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, VintedSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, host: str) -> VintedSession:
        async with self._lock:
            session = self._sessions.get(host)
            if session is None:
                session = VintedSession(host, self.settings)
                self._sessions[host] = session
            return session

    def status(self) -> dict[str, str]:
        return {host: session.status_line() for host, session in self._sessions.items()}

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
