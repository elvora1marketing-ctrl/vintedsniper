"""Tests für die Proxy-Übersetzung ins Playwright-Format.

Playwright ignoriert Zugangsdaten, die in der Server-URL stecken. Da praktisch
jeder Residential-Proxy Zugangsdaten hat, wäre der Browser-Modus mit Proxy sonst
komplett unbrauchbar — und genau der ist der Ausweg bei gesperrter Server-IP.
"""

import unittest

from vinted_sniper.vinted.browser import playwright_proxy


class PlaywrightProxyTests(unittest.TestCase):
    def test_zugangsdaten_werden_abgetrennt(self):
        config = playwright_proxy("http://benutzer:geheim@proxy.example.com:8000")
        self.assertEqual(config["server"], "http://proxy.example.com:8000")
        self.assertEqual(config["username"], "benutzer")
        self.assertEqual(config["password"], "geheim")
        # Die Zugangsdaten dürfen nicht zusätzlich im Server stehen.
        self.assertNotIn("benutzer", config["server"])
        self.assertNotIn("geheim", config["server"])

    def test_ohne_zugangsdaten(self):
        config = playwright_proxy("http://proxy.example.com:8000")
        self.assertEqual(config, {"server": "http://proxy.example.com:8000"})

    def test_socks5_bleibt_erhalten(self):
        config = playwright_proxy("socks5://benutzer:geheim@proxy.example.com:1080")
        self.assertEqual(config["server"], "socks5://proxy.example.com:1080")
        self.assertEqual(config["username"], "benutzer")

    def test_ohne_schema_wird_http_angenommen(self):
        config = playwright_proxy("proxy.example.com:8000")
        self.assertEqual(config["server"], "http://proxy.example.com:8000")

    def test_sonderzeichen_im_passwort(self):
        # Passwörter mit @ oder : müssen in der URL kodiert sein; beim
        # Weiterreichen an Playwright müssen sie wieder dekodiert ankommen.
        config = playwright_proxy("http://user:p%40ss%3Aword@proxy.example.com:8000")
        self.assertEqual(config["password"], "p@ss:word")
        self.assertEqual(config["server"], "http://proxy.example.com:8000")

    def test_ohne_port(self):
        config = playwright_proxy("http://proxy.example.com")
        self.assertEqual(config["server"], "http://proxy.example.com")


if __name__ == "__main__":
    unittest.main()
