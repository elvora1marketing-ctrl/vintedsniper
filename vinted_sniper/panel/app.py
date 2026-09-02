"""Der Webserver des Panels.

`aiohttp` ist über discord.py ohnehin installiert — es kommt also keine neue
Abhängigkeit dazu. Der Server läuft im selben Event-Loop wie der Sniper und
benutzt dieselbe Datenbank und denselben Monitor: was hier geändert wird, wirkt
sofort, ohne Neustart.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiohttp import web

from .. import bulk, report
from ..db import Database
from ..monitor import Monitor
from ..vinted import domains
from ..vinted.client import VintedClient
from ..vinted.session import VintedError
from ..vinted.urls import InvalidSearchURL, parse_search_url
from . import auth, views

log = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class PanelServer:
    """Kleines Verwaltungs-Panel neben dem Sniper."""

    def __init__(
        self,
        *,
        db: Database,
        monitor: Monitor,
        client: VintedClient,
        password: str,
        alert_webhook_url: str,
        host: str,
        port: int,
        min_interval: int,
        default_interval: int,
        started_at: Any,
        default_countries: tuple[str, ...] = (),
        settings: Any = None,
    ) -> None:
        self.db = db
        self.monitor = monitor
        self.client = client
        self.password = password
        self.alert_webhook_url = alert_webhook_url
        self.host = host
        self.port = port
        self.min_interval = min_interval
        self.default_interval = default_interval
        self.started_at = started_at
        self.default_countries = default_countries
        # Für den Betriebsbericht auf der Übersicht. Ohne Einstellungen
        # (Tests) fehlt nur der Kasten.
        self.settings = settings

        self._runner: web.AppRunner | None = None

    # ----------------------------------------------------------------- Aufbau

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware])
        app.add_routes(
            [
                web.get("/", self.dashboard),
                web.get("/login", self.login_form),
                web.post("/login", self.login),
                web.post("/logout", self.logout),
                web.post("/add", self.add_watch),
                web.post("/import", self.import_watches),
                web.post("/bulk", self.bulk_action),
                web.post("/watch/{watch_id}/toggle", self.toggle_watch),
                web.post("/watch/{watch_id}/delete", self.delete_watch),
                web.post("/watch/{watch_id}/interval", self.set_interval),
                web.get("/health", self.health),
            ]
        )
        return app

    async def start(self) -> None:
        if not self.password:
            log.warning(
                "PANEL_PASSWORD ist nicht gesetzt — das Panel bleibt aus. Ohne "
                "Passwort könnte jeder die Suchen ändern, der die Adresse kennt."
            )
            return

        self._runner = web.AppRunner(self._build_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("Panel läuft auf http://%s:%d", self.host, self.port)

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------- Auth

    @web.middleware
    async def _auth_middleware(
        self, request: web.Request, handler: Handler
    ) -> web.StreamResponse:
        offen = {"/login", "/health"}
        if request.path in offen:
            return await handler(request)

        if auth.token_valid(self.password, request.cookies.get(auth.COOKIE_NAME)):
            return await handler(request)

        if request.method == "POST":
            # Abgelaufene Sitzung mitten im Formular: zur Anmeldung, nicht in
            # einen kommentarlosen Fehler.
            raise web.HTTPFound("/login")
        return web.Response(
            text=views.login_page(), content_type="text/html", charset="utf-8"
        )

    async def login_form(self, request: web.Request) -> web.StreamResponse:
        if auth.token_valid(self.password, request.cookies.get(auth.COOKIE_NAME)):
            raise web.HTTPFound("/")
        return web.Response(
            text=views.login_page(), content_type="text/html", charset="utf-8"
        )

    async def login(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        entered = str(form.get("password", ""))

        if not auth.password_matches(self.password, entered):
            return web.Response(
                text=views.login_page(error="Passwort stimmt nicht."),
                content_type="text/html",
                charset="utf-8",
                status=401,
            )

        response = web.HTTPFound("/")
        response.set_cookie(
            auth.COOKIE_NAME,
            auth.make_token(self.password),
            max_age=auth.SESSION_TTL,
            httponly=True,
            # Verhindert, dass eine fremde Seite Formulare an dieses Panel
            # schickt und der Browser die Sitzung mitsendet.
            samesite="Strict",
            secure=True,
        )
        raise response

    async def logout(self, request: web.Request) -> web.StreamResponse:
        response = web.HTTPFound("/login")
        response.del_cookie(auth.COOKIE_NAME)
        raise response

    async def health(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"status": "ok"})

    # ------------------------------------------------------------------ Seiten

    async def dashboard(self, request: web.Request) -> web.StreamResponse:
        watches = await self.db.list_watches()
        running = {w.id for w in watches if self.monitor.is_running(w.id)}
        betrieb = (
            report.rows(
                self.settings, watches, self.monitor.profiles,
                meter=self.client.pool.meter,
            )
            if self.settings is not None
            else []
        )
        return web.Response(
            text=views.dashboard(
                watches=watches,
                running=running,
                sessions=self.client.pool.status(),
                started_at=self.started_at,
                message=request.query.get("ok"),
                error=request.query.get("err"),
                default_countries=self.default_countries,
                traffic_line=self.client.pool.meter.summary(),
                betrieb=betrieb,
                paused=getattr(self.monitor, "paused", False),
            ),
            content_type="text/html",
            charset="utf-8",
        )

    # ------------------------------------------------------------------ Aktionen

    @staticmethod
    def _back(*, ok: str | None = None, err: str | None = None) -> web.HTTPFound:
        """Zurück zur Übersicht, mit einer Meldung in der Adresszeile.

        Nach einer Änderung wird umgeleitet statt direkt gerendert, damit ein
        Neuladen der Seite die Aktion nicht wiederholt.
        """
        if ok:
            return web.HTTPFound(f"/?ok={quote(ok)}")
        if err:
            return web.HTTPFound(f"/?err={quote(err)}")
        return web.HTTPFound("/")

    def _extra_domains(self, form: Any) -> tuple[list[Any], list[str]]:
        """Weitere Länder aus dem Formular lesen.

        Kommt sowohl mit Ankreuzfeldern (mehrfach `laender`) als auch mit einem
        getippten `fr, nl, it` zurecht.
        """
        werte = form.getall("laender") if hasattr(form, "getall") else []
        text = ",".join(str(w) for w in werte) or str(form.get("laender", ""))
        return domains.parse_list(text)

    async def _anlegen(
        self, query: Any, *, name: str, interval: int
    ) -> Any:
        """Eine Suche speichern und sofort starten."""
        watch = await self.db.add_watch(
            guild_id=0,
            channel_id=0,
            creator_id=0,
            name=name[:80],
            query=query,
            source_url=query.web_url(),
            interval=interval,
            webhook_url=self.alert_webhook_url,
            origin="panel",
        )
        self.monitor.start(watch)
        return watch

    async def add_watch(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        raw_url = str(form.get("url", "")).strip()
        name = str(form.get("name", "")).strip()
        interval_raw = str(form.get("interval", "")).strip()

        if not self.alert_webhook_url:
            # Ohne Ziel würde die Suche zwar laufen, aber ins Leere melden —
            # das fiele erst beim ersten Treffer auf.
            raise self._back(
                err="Es fehlt ein Alert-Ziel. Trage ALERT_WEBHOOK_URL in der "
                ".env ein und starte neu, sonst kommen die Treffer nirgends an."
            )

        try:
            query = parse_search_url(raw_url)
        except InvalidSearchURL as exc:
            raise self._back(err=str(exc)) from exc

        interval = self.default_interval
        if interval_raw.isdigit():
            interval = max(self.min_interval, int(interval_raw))

        extra, unbekannt = self._extra_domains(form)
        if unbekannt:
            raise self._back(
                err=f"Unbekannte Länder: {', '.join(unbekannt)}. "
                "Erlaubt sind Kürzel wie fr, nl, it oder uk."
            )

        # Einmal live abfragen: so merkt man sofort, ob die URL taugt, statt es
        # erst beim ersten stillen Durchlauf zu erfahren. Geprüft wird nur die
        # Ausgangsdomain — die gespiegelten benutzen dieselben Filter.
        try:
            items = await self.client.search(query)
        except VintedError as exc:
            raise self._back(err=f"Testabfrage fehlgeschlagen: {exc}") from exc

        basis = name or query.scalars.get("search_text") or query.host
        queries = bulk.expand(query, extra)
        angelegt = []
        for einzeln in queries:
            beschriftung = (
                f"{basis} {einzeln.domain.flag}" if len(queries) > 1 else basis
            )
            angelegt.append(
                await self._anlegen(einzeln, name=beschriftung, interval=interval)
            )

        if len(angelegt) == 1:
            meldung = (
                f"Suche #{angelegt[0].id} angelegt, "
                f"{len(items)} Artikel als Ausgangsbestand."
            )
        else:
            laender = " ".join(q.domain.flag for q in queries)
            meldung = (
                f"{len(angelegt)} Suchen angelegt — {laender}. "
                f"{len(items)} Artikel als Ausgangsbestand auf {query.host}."
            )
        hinweis = bulk.currency_warning(query, extra)
        raise self._back(ok=f"{meldung} {hinweis}".strip())

    async def import_watches(self, request: web.Request) -> web.StreamResponse:
        """Mehrere Such-URLs auf einmal übernehmen, eine je Zeile."""
        form = await request.post()
        text = str(form.get("urls", ""))
        interval_raw = str(form.get("interval", "")).strip()

        if not self.alert_webhook_url:
            raise self._back(
                err="Es fehlt ein Alert-Ziel. Trage ALERT_WEBHOOK_URL in der "
                ".env ein und starte neu, sonst kommen die Treffer nirgends an."
            )

        plan = bulk.parse_import(text)
        if not plan.entries and not plan.problems:
            raise self._back(err="Keine Adresse gefunden — das Feld war leer.")

        interval = self.default_interval
        if interval_raw.isdigit():
            interval = max(self.min_interval, int(interval_raw))

        # Anders als beim einzelnen Hinzufügen wird hier nicht live geprüft: bei
        # fünfzig Adressen wären das fünfzig Abfragen auf einen Schlag — ein
        # zuverlässiger Weg, sich von Vinted sperren zu lassen. Taugt eine Suche
        # nicht, zeigt die Übersicht sie nach dem ersten Durchlauf als Fehler.
        extra, unbekannt = self._extra_domains(form)
        if unbekannt:
            raise self._back(
                err=f"Unbekannte Länder: {', '.join(unbekannt)}. "
                "Erlaubt sind Kürzel wie fr, nl, it oder uk."
            )

        vorhanden = {w.source_url for w in await self.db.list_watches()}
        angelegt = 0
        bekannt = 0

        for entry in plan.entries:
            queries = bulk.expand(entry.query, extra)
            for einzeln in queries:
                url = einzeln.web_url()
                if url in vorhanden:
                    bekannt += 1
                    continue
                vorhanden.add(url)
                await self._anlegen(
                    einzeln,
                    name=(
                        f"{entry.name} {einzeln.domain.flag}"
                        if len(queries) > 1
                        else entry.name
                    ),
                    interval=interval,
                )
                angelegt += 1

        meldung = bulk.summarize(plan, angelegt=angelegt, bekannt=bekannt)
        if plan.problems:
            # Die ersten Fehlerzeilen mitgeben — pauschal „3 fehlerhaft“ lässt
            # niemanden erkennen, welche Zeile gemeint ist.
            zeilen = "; ".join(p.describe() for p in plan.problems[:3])
            if len(plan.problems) > 3:
                zeilen += f"; … und {len(plan.problems) - 3} weitere"
            raise self._back(err=f"{meldung} {zeilen}")
        raise self._back(ok=meldung)

    async def bulk_action(self, request: web.Request) -> web.StreamResponse:
        """Mehrere Suchen auf einmal pausieren, fortsetzen oder löschen.

        Bei 21 Suchen aus sieben Ländern ist alles andere Klickarbeit.
        """
        form = await request.post()
        aktion = str(form.get("action", "")).strip()
        if aktion not in ("pause", "resume", "delete"):
            raise self._back(err="Unbekannte Aktion.")

        ids: list[int] = []
        for roh in form.getall("ids", []):
            text = str(roh).strip()
            if text.isdigit():
                ids.append(int(text))
        if not ids:
            raise self._back(err="Es war keine Suche ausgewählt.")

        betroffen = 0
        for watch_id in dict.fromkeys(ids):
            watch = await self.db.get_watch(watch_id)
            if watch is None:
                continue
            if aktion == "delete":
                self.monitor.stop(watch.id)
                await self.db.delete_watch(watch.id)
            elif aktion == "pause":
                self.monitor.stop(watch.id)
                await self.db.set_enabled(watch.id, False)
            else:
                await self.db.set_enabled(watch.id, True)
                aktualisiert = await self.db.get_watch(watch.id)
                if aktualisiert is not None:
                    self.monitor.start(aktualisiert)
            betroffen += 1

        wort = {"delete": "gelöscht", "pause": "pausiert", "resume": "laufen wieder"}
        if not betroffen:
            raise self._back(err="Keine der ausgewählten Suchen gibt es noch.")

        hinweis = ""
        if aktion == "delete":
            # Datei-Suchen kommen beim nächsten Start zurück — das erklärt
            # sonst niemand, und der Nutzer hält es für einen Fehler.
            hinweis = (
                " Suchen aus searches.toml legt der nächste Start neu an; "
                "nimm sie dort heraus, wenn sie weg bleiben sollen."
            )
        raise self._back(ok=f"{betroffen} Suche(n) {wort[aktion]}.{hinweis}")

    async def _watch_from(self, request: web.Request):
        try:
            watch_id = int(request.match_info["watch_id"])
        except (KeyError, ValueError) as exc:
            raise self._back(err="Ungültige Such-ID.") from exc
        watch = await self.db.get_watch(watch_id)
        if watch is None:
            raise self._back(err=f"Suche #{watch_id} gibt es nicht.")
        return watch

    async def toggle_watch(self, request: web.Request) -> web.StreamResponse:
        watch = await self._watch_from(request)
        neu = not watch.enabled
        await self.db.set_enabled(watch.id, neu)

        if neu:
            refreshed = await self.db.get_watch(watch.id)
            if refreshed is not None:
                self.monitor.start(refreshed)
            raise self._back(ok=f"Suche #{watch.id} läuft wieder.")

        self.monitor.stop(watch.id)
        raise self._back(ok=f"Suche #{watch.id} pausiert.")

    async def delete_watch(self, request: web.Request) -> web.StreamResponse:
        watch = await self._watch_from(request)
        self.monitor.stop(watch.id)
        await self.db.delete_watch(watch.id)
        raise self._back(ok=f"Suche #{watch.id} gelöscht.")

    async def set_interval(self, request: web.Request) -> web.StreamResponse:
        watch = await self._watch_from(request)
        form = await request.post()
        raw = str(form.get("interval", "")).strip()

        if not raw.isdigit():
            raise self._back(err="Intervall muss eine Zahl sein.")
        interval = int(raw)
        if interval < self.min_interval:
            raise self._back(
                err=f"Minimum sind {self.min_interval}s — kürzere Abstände "
                "provozieren nur Sperren durch Vinted."
            )

        await self.db.set_interval(watch.id, interval)
        refreshed = await self.db.get_watch(watch.id)
        if refreshed is not None and refreshed.enabled:
            self.monitor.start(refreshed)
        raise self._back(ok=f"Suche #{watch.id} prüft jetzt alle {interval}s.")
