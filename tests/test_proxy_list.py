"""Tests für das Einlesen von Proxy-Listen.

Anbieter liefern unterschiedliche Schreibweisen. Wird eine davon falsch
verstanden, bekommt der Bot keine Verbindung — und das ist bei gesperrter
Server-IP der einzige Weg, der noch offen steht.
"""

import tempfile
import unittest
from pathlib import Path

from vinted_sniper.proxies import load_proxies, parse_proxy_line


class ParseProxyLineTests(unittest.TestCase):
    def test_webshare_format(self):
        # host:port:benutzer:passwort — so liefert Webshare seine Listen aus.
        self.assertEqual(
            parse_proxy_line("p.webshare.io:80:ckrgzvap-DE-1:geheim"),
            "http://ckrgzvap-DE-1:geheim@p.webshare.io:80",
        )

    def test_url_format_bleibt_unveraendert(self):
        url = "http://benutzer:geheim@proxy.example.com:8000"
        self.assertEqual(parse_proxy_line(url), url)

    def test_socks5_url_bleibt_unveraendert(self):
        url = "socks5://benutzer:geheim@proxy.example.com:1080"
        self.assertEqual(parse_proxy_line(url), url)

    def test_ohne_zugangsdaten(self):
        self.assertEqual(
            parse_proxy_line("proxy.example.com:8000"),
            "http://proxy.example.com:8000",
        )

    def test_sonderzeichen_werden_kodiert(self):
        # Ein @ oder : im Passwort würde die URL sonst zerlegen.
        with self.assertLogs("vinted_sniper.proxies", level="WARNING"):
            # Fünf Teile sind mehrdeutig — lieber ablehnen als raten.
            self.assertIsNone(parse_proxy_line("host:80:benutzer:p@ss:wort"))

        parsed = parse_proxy_line("host:80:be@nutzer:pass")
        assert parsed is not None
        self.assertEqual(parsed, "http://be%40nutzer:pass@host:80")

    def test_leerzeilen_und_kommentare(self):
        self.assertIsNone(parse_proxy_line(""))
        self.assertIsNone(parse_proxy_line("   "))
        self.assertIsNone(parse_proxy_line("# Kommentar"))

    def test_unverstaendliche_zeile(self):
        with self.assertLogs("vinted_sniper.proxies", level="WARNING"):
            self.assertIsNone(parse_proxy_line("völliger unsinn"))


class LoadProxiesTests(unittest.TestCase):
    def test_aus_der_env(self):
        proxies = load_proxies(
            inline="host1:80:u:p, http://host2:8000", path=None
        )
        self.assertEqual(
            proxies, ["http://u:p@host1:80", "http://host2:8000"]
        )

    def test_aus_einer_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text(
                "# Webshare DE\n"
                "p.webshare.io:80:ckrgzvap-DE-1:geheim\n"
                "p.webshare.io:80:ckrgzvap-DE-2:geheim\n"
                "\n"
                "p.webshare.io:80:ckrgzvap-DE-3:geheim\n"
            )
            proxies = load_proxies(inline="", path=path)
        self.assertEqual(len(proxies), 3)
        self.assertEqual(proxies[0], "http://ckrgzvap-DE-1:geheim@p.webshare.io:80")
        self.assertEqual(proxies[2], "http://ckrgzvap-DE-3:geheim@p.webshare.io:80")

    def test_datei_und_env_werden_zusammengefuehrt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text("host2:80:u:p\n")
            proxies = load_proxies(inline="host1:80:u:p", path=path)
        self.assertEqual(len(proxies), 2)
        # Die Reihenfolge zählt: der Bot arbeitet die Liste der Reihe nach ab.
        self.assertTrue(proxies[0].endswith("@host1:80"))

    def test_doppelte_fliegen_raus(self):
        proxies = load_proxies(
            inline="host:80:u:p,host:80:u:p,http://u:p@host:80", path=None
        )
        self.assertEqual(len(proxies), 1)

    def test_fehlende_datei_ist_kein_fehler(self):
        self.assertEqual(load_proxies(inline="", path=Path("/nicht/da.txt")), [])

    def test_verzeichnis_statt_datei_wird_uebersprungen(self):
        # Docker legt bei einem Bind-Mount auf eine fehlende Datei ein
        # Verzeichnis an — das darf den Start nicht verhindern.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("vinted_sniper.proxies", level="WARNING"):
                proxies = load_proxies(inline="", path=Path(tmp))
        self.assertEqual(proxies, [])

    def test_grosse_liste(self):
        # Der reale Fall: mehrere tausend Sticky-Sessions eines Anbieters.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text(
                "\n".join(
                    f"p.webshare.io:80:ckrgzvap-DE-{index}:geheim"
                    for index in range(1, 1001)
                )
            )
            proxies = load_proxies(inline="", path=path)
        self.assertEqual(len(proxies), 1000)


if __name__ == "__main__":
    unittest.main()
