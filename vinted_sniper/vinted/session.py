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
* **Browser-Modus** — blockt Vinted den HTTP-Weg zweimal in Folge, laufen alle
  weiteren Abfragen dauerhaft in einem echten Chromium (siehe `browser.py`).
  Cookies aus dem Browser in einen HTTP-Client zu tragen reicht gegen Datadome
  nicht; die Anfrage muss tatsächlich aus dem Browser kommen.
* **Proxy-Rotation und Backoff** — bei 403/429 wird die Ausgangs-IP gewechselt
  und exponentiell zurückgefahren, statt stumpf weiterzuhämmern.

Es gibt keinen Zustand, in dem der Sniper dauerhaft „tot" ist: jeder Blocker
löst einen Reparaturpfad aus. Was hier allerdings niemand lösen kann, ist eine
gesperrte Server-IP — dagegen hilft ausschließlich ein Proxy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

from curl_cffi.requests import AsyncSession

from ..config import Settings
from . import domains
from .browser import BrowserFetcher, BrowserUnavailable

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

    BASE_ATTEMPTS = 4
    # Ruhezeit, nachdem eine IP-Sperre festgestellt wurde.
    IP_BLOCK_COOLDOWN = 600.0

    @property
    def max_attempts(self) -> int:
        """Genug Versuche, um jeden Proxy einmal durchzuprobieren.

        Mit vier festen Versuchen käme bei fünf hinterlegten Proxies nie die
        ganze Liste dran — der Bot würde aufgeben, obwohl ein funktionierender
        Ausgang noch ungenutzt in der Liste steht.
        """
        return max(self.BASE_ATTEMPTS, len(self.settings.proxies) + 1)

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

        # Sobald HTTP zuverlässig blockt, läuft alles über den Browser. Der ist
        # langsamer, aber die Alternative ist, gar keine Treffer zu bekommen.
        self._browser: BrowserFetcher | None = None
        self._browser_mode = False
        # Steht die IP auf Vinteds Sperrliste, hilft kein Wiederholen. Statt im
        # Sekundentakt weiterzuklopfen (und das Log zuzumüllen), wird für eine
        # Weile gar nicht erst angefragt.
        self._blocked_until = 0.0
        # Indizes von Proxys, die nicht funktionieren (Limit erreicht,
        # Session abgelaufen). Werden bei der Rotation übersprungen.
        self._dead_proxies: set[int] = set()

    # ------------------------------------------------------------------ Status

    @property
    def healthy(self) -> bool:
        return self._consecutive_blocks == 0 and (
            self._session is not None or self._browser_mode
        )

    def status_line(self) -> str:
        remaining = self._blocked_until - time.monotonic()
        if remaining > 0:
            return f"IP gesperrt, Pause noch {int(remaining / 60)} Min"
        if self._browser_mode:
            state = "Browser-Modus"
        elif self._session is None:
            return "nicht initialisiert"
        else:
            state = "HTTP"
        if self._consecutive_blocks:
            return f"{state}, blockiert ({self._consecutive_blocks}× in Folge)"
        age = int(time.monotonic() - self._bootstrapped_at)
        return f"{state}, ok (Session {age}s alt)"

    # ------------------------------------------------------------- Netzwerk-Setup

    def _current_proxy(self) -> dict[str, str] | None:
        if not self.settings.proxies:
            return None
        proxy = self.settings.proxies[self._proxy_index % len(self.settings.proxies)]
        return {"http": proxy, "https": proxy}

    def _mark_proxy_dead(self) -> None:
        """Den aktuellen Proxy aus dem Rennen nehmen.

        Bei großen Anbieterlisten sind einzelne Einträge regelmäßig unbrauchbar
        (Bandbreite aufgebraucht, Session abgelaufen). Solche Proxys immer
        wieder anzufassen kostet bei jedem Durchlauf Zeit.
        """
        if not self.settings.proxies:
            return
        index = self._proxy_index % len(self.settings.proxies)
        if index not in self._dead_proxies:
            self._dead_proxies.add(index)
            log.warning(
                "[%s] Proxy #%d ist unbrauchbar und wird übersprungen "
                "(%d von %d noch nutzbar).",
                self.host,
                index + 1,
                len(self.settings.proxies) - len(self._dead_proxies),
                len(self.settings.proxies),
            )
        if len(self._dead_proxies) >= len(self.settings.proxies):
            # Alle als tot markiert: eher ein allgemeines Netzproblem als
            # tatsächlich lauter kaputte Proxys — noch einmal von vorn.
            log.warning(
                "[%s] Alle Proxys waren erfolglos — Liste wird zurückgesetzt.",
                self.host,
            )
            self._dead_proxies.clear()

    def _rotate_proxy(self) -> None:
        """Auf den nächsten nutzbaren Proxy weiterschalten.

        Bei genau einem oder gar keinem Proxy gibt es nichts zu wechseln — dann
        bleibt es beim Backoff.
        """
        total = len(self.settings.proxies)
        if total <= 1:
            return

        for _ in range(total):
            self._proxy_index += 1
            if self._proxy_index % total not in self._dead_proxies:
                break

        log.info(
            "[%s] Wechsle auf Proxy #%d von %d.",
            self.host,
            self._proxy_index % total + 1,
            total,
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

    async def _bootstrap(self) -> None:
        """Frische HTTP-Session mit gültigen Cookies herstellen.

        Wirft `Blocked`, wenn Vinted schon die Startseite verweigert — dann
        übernimmt der Browser-Modus.
        """
        await self.close()
        session = await self._new_session()

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
        if challenged or response.status_code >= 400:
            await session.close()
            raise Blocked(
                f"{self.host} verweigert schon die Startseite "
                f"(HTTP {response.status_code})."
            )

        self._session = session
        self._bootstrapped_at = time.monotonic()
        log.info("[%s] Session per HTTP aufgebaut.", self.host)

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

    # -------------------------------------------------------------- Browser-Modus

    async def _enable_browser_mode(self) -> None:
        """Dauerhaft auf Abfragen im echten Browser umstellen."""
        if not self._browser_mode:
            log.warning(
                "[%s] HTTP wird durchgehend blockiert — schalte auf Browser-Modus um.",
                self.host,
            )
            self._browser_mode = True
        await self._restart_browser()

    async def _restart_browser(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        proxy = None
        if self.settings.proxies:
            proxy = self.settings.proxies[self._proxy_index % len(self.settings.proxies)]

        fetcher = BrowserFetcher(
            self.host,
            language=self.domain.language,
            proxy=proxy,
            timeout=max(self.settings.request_timeout, 45.0),
        )
        try:
            await fetcher.start()
        except BrowserUnavailable as exc:
            await fetcher.close()
            # Wenn selbst ein echter Browser abgewiesen wird, liegt es nicht am
            # Fingerprint, sondern an der IP. Weiterprobieren bringt nichts.
            self._blocked_until = time.monotonic() + self.IP_BLOCK_COOLDOWN
            self._browser_mode = False
            raise Blocked(str(exc)) from exc
        except Exception as exc:
            await fetcher.close()
            raise Blocked(f"Browser-Sitzung fehlgeschlagen: {exc}") from exc

        self._browser = fetcher
        self._bootstrapped_at = time.monotonic()

    async def _get_json_via_browser(
        self, path: str, params: dict[str, str]
    ) -> dict[str, Any]:
        if self._browser is None or not self._browser.running:
            await self._restart_browser()
        assert self._browser is not None

        await self._limiter.acquire()
        status, body = await self._browser.fetch_json(path, params)

        if status in (403, 429):
            raise Blocked(f"HTTP {status} von {self.host} (Browser-Modus).")
        if status == 0:
            raise Blocked(f"Browser-Abfrage fehlgeschlagen: {body[:200]}")
        if status >= 500:
            raise Blocked(f"Vinted-Serverfehler (HTTP {status}).")
        if status >= 400:
            raise VintedError(
                f"Vinted antwortet mit HTTP {status}. Stimmen die Filter in "
                "der Such-URL?"
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise Blocked(f"Antwort war kein JSON: {exc}") from exc

        if self._consecutive_blocks:
            log.info("[%s] Browser-Modus liefert wieder Daten.", self.host)
        self._consecutive_blocks = 0
        self._blocked_until = 0.0
        return payload

    # ------------------------------------------------------------------ Requests

    async def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET auf einen API-Pfad mit allen Reparaturpfaden.

        Wirft `Blocked`, wenn auch nach Refresh, Proxy-Wechsel und
        Browser-Fallback nichts durchgeht.
        """
        async with self._lock:
            remaining = self._blocked_until - time.monotonic()
            if remaining > 0:
                raise Blocked(
                    f"{self.host} sperrt diese IP. Nächster Versuch in "
                    f"{int(remaining / 60)} Min. Ein Proxy (PROXIES in der .env) "
                    "oder ein Betrieb außerhalb des Rechenzentrums löst das."
                )

            last_error: Exception | None = None

            for attempt in range(1, self.max_attempts + 1):
                if self._browser_mode:
                    try:
                        return await self._get_json_via_browser(path, params)
                    except Blocked as exc:
                        last_error = exc
                        self._consecutive_blocks += 1
                        self._rotate_proxy()
                        await self._backoff(attempt)
                        await self._restart_browser()
                        continue

                try:
                    await self._ensure_session()
                except Blocked as exc:
                    last_error = exc
                    self._consecutive_blocks += 1
                    log.warning("[%s] %s", self.host, exc)
                    if self._playwright_available:
                        await self._enable_browser_mode()
                        continue
                    self._rotate_proxy()
                    await self._backoff(attempt)
                    continue
                except VintedError as exc:
                    # Keine Blockade, sondern die Verbindung selbst scheitert —
                    # bei gesetztem Proxy heißt das praktisch immer: dieser
                    # Proxy ist kaputt (Limit erreicht, abgelaufen, tot). Ohne
                    # Weiterschalten bliebe die Suche für immer daran hängen.
                    last_error = exc
                    log.warning("[%s] %s", self.host, exc)
                    self._mark_proxy_dead()
                    self._rotate_proxy()
                    continue

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
                    # Zwei Blockaden in Folge heißen: HTTP kommt hier nicht
                    # durch. Ab dann läuft alles im Browser — Cookies allein
                    # reichen gegen diese Erkennung nicht.
                    if self._consecutive_blocks >= 2 and self._playwright_available:
                        await self._enable_browser_mode()
                    else:
                        await self._bootstrap()
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
                self._blocked_until = 0.0
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
        if self._browser is not None:
            await self._browser.close()
            self._browser = None


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
