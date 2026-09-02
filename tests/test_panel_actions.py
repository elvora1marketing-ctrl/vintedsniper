"""Die Formular-Aktionen des Panels, ohne echten Webserver.

`aiohttp` wird durch einen kleinen Stub ersetzt, der sich an den Stellen
genauso verhält wie das Original — vor allem `getall`, das ohne Vorgabe einen
KeyError wirft, wenn das Feld fehlt. Genau daran ist der Import einmal
gescheitert, sobald niemand ein Land angekreuzt hatte.
"""

import datetime as dt
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()

for name in ("dotenv",):
    if name not in sys.modules:
        modul = types.ModuleType(name)
        modul.load_dotenv = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules[name] = modul
if "curl_cffi" not in sys.modules:
    cc = types.ModuleType("curl_cffi")
    req = types.ModuleType("curl_cffi.requests")
    req.AsyncSession = object  # type: ignore[attr-defined]
    cc.requests = req  # type: ignore[attr-defined]
    sys.modules["curl_cffi"] = cc
    sys.modules["curl_cffi.requests"] = req


class HTTPException(Exception):
    pass


class HTTPFound(HTTPException):
    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


class Response:
    def __init__(self, **kw) -> None:
        self.kw = kw


if "aiohttp" not in sys.modules:
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")
    web.HTTPException = HTTPException  # type: ignore[attr-defined]
    web.HTTPFound = HTTPFound  # type: ignore[attr-defined]
    web.Response = Response  # type: ignore[attr-defined]
    web.StreamResponse = object  # type: ignore[attr-defined]
    web.Request = object  # type: ignore[attr-defined]
    web.json_response = lambda d: d  # type: ignore[attr-defined]
    web.Application = object  # type: ignore[attr-defined]
    web.AppRunner = object  # type: ignore[attr-defined]
    web.TCPSite = object  # type: ignore[attr-defined]
    web.get = web.post = lambda *a, **k: None  # type: ignore[attr-defined]
    web.middleware = lambda f: f  # type: ignore[attr-defined]
    aiohttp.web = web  # type: ignore[attr-defined]
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web

from vinted_sniper.db import Database  # noqa: E402
from vinted_sniper.monitor import Monitor  # noqa: E402
from vinted_sniper.panel.app import PanelServer  # noqa: E402

_FEHLT = object()


class Form(dict):
    """Verhält sich wie aiohttps MultiDictProxy: `getall` ohne Vorgabe wirft."""

    def getall(self, key, default=_FEHLT):
        if key in self:
            wert = self[key]
            return list(wert) if isinstance(wert, list) else [wert]
        if default is _FEHLT:
            raise KeyError(key)
        return default


class Request:
    def __init__(self, form: Form, path: str = "/") -> None:
        self._form = form
        self.path = path
        self.method = "POST"
        self.match_info: dict[str, str] = {}

    async def post(self) -> Form:
        return self._form


class PanelActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "t.db")
        await self.db.connect()
        settings = SimpleNamespace(
            dedupe_scope="all", polling_enabled=False, active_hours=None,
        )
        monitor = Monitor(
            settings, self.db, None, on_items=None, on_trouble=None, on_recovered=None
        )
        meter = SimpleNamespace(summary=lambda: "x", requests=0)
        client = SimpleNamespace(pool=SimpleNamespace(meter=meter, status=lambda: {}))
        self.panel = PanelServer(
            db=self.db, monitor=monitor, client=client, password="pw",
            alert_webhook_url="https://discord.com/api/webhooks/1/a",
            host="h", port=1, min_interval=20, default_interval=60,
            started_at=dt.datetime.now(dt.timezone.utc), settings=settings,
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def umleitung(self, coro) -> str:
        with self.assertRaises(HTTPFound) as ctx:
            await coro
        return ctx.exception.location

    async def test_import_ohne_angekreuztes_land(self):
        # Der Normalfall: niemand hat ein Land angekreuzt, `laender` fehlt im
        # Formular komplett.
        ziel = await self.umleitung(
            self.panel.import_watches(
                Request(Form(urls="https://www.vinted.de/catalog?search_text=rl+zip"))
            )
        )
        self.assertIn("ok=", ziel)
        self.assertEqual(len(await self.db.list_watches()), 1)

    async def test_import_mit_laendern(self):
        ziel = await self.umleitung(
            self.panel.import_watches(
                Request(
                    Form(
                        urls="https://www.vinted.de/catalog?search_text=rl+zip",
                        laender=["fr", "it"],
                    )
                )
            )
        )
        self.assertIn("ok=", ziel)
        hosts = sorted(w.host for w in await self.db.list_watches())
        self.assertEqual(hosts, ["www.vinted.de", "www.vinted.fr", "www.vinted.it"])

    async def test_import_leer(self):
        ziel = await self.umleitung(self.panel.import_watches(Request(Form(urls=""))))
        self.assertIn("err=", ziel)

    async def test_import_doppelte_zeile_wird_nur_einmal_angelegt(self):
        url = "https://www.vinted.de/catalog?search_text=rl+zip"
        await self.umleitung(
            self.panel.import_watches(Request(Form(urls=f"{url}\n{url}")))
        )
        ziel = await self.umleitung(self.panel.import_watches(Request(Form(urls=url))))
        self.assertEqual(len(await self.db.list_watches()), 1)
        self.assertIn("ok=", ziel)

    async def test_hinzufuegen_ohne_land(self):
        # `/add` liest die Länder auf demselben Weg — und prüft die Adresse
        # live; ohne Client bleibt nur der Weg bis zur Prüfung.
        self.panel.client = SimpleNamespace(
            pool=SimpleNamespace(meter=None, status=lambda: {}),
            search=None,
        )

        async def keine_pruefung(query):
            return []

        self.panel.client.search = keine_pruefung
        ziel = await self.umleitung(
            self.panel.add_watch(
                Request(Form(url="https://www.vinted.de/catalog?search_text=rl+zip"))
            )
        )
        self.assertIn("ok=", ziel)


if __name__ == "__main__":
    unittest.main()
