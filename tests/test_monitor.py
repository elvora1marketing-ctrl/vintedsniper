"""Tests für die Reihenfolge, in der Funde gemeldet werden."""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiosqlite_stub import install  # noqa: E402

install()

# Der Monitor zieht über die Konfiguration und den Vinted-Client zwei Pakete
# nach, die es ohne Netz nicht braucht. Hier geht es nur um die Reihenfolge.
if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["dotenv"] = dotenv
if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests = types.ModuleType("curl_cffi.requests")
    requests.AsyncSession = object  # type: ignore[attr-defined]
    curl_cffi.requests = requests  # type: ignore[attr-defined]
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests

from vinted_sniper import deals  # noqa: E402
from vinted_sniper.monitor import Monitor  # noqa: E402
from vinted_sniper.profiles import load_profiles  # noqa: E402
from vinted_sniper.vinted.models import Item  # noqa: E402

BEISPIEL = Path(__file__).resolve().parent.parent / "profiles.example.toml"


def fund(nummer, titel, preis, groesse="L"):
    return Item(
        id=str(nummer), host="www.vinted.de", title=titel,
        url=f"https://www.vinted.de/items/{nummer}", price=preis, total_price=None,
        currency="EUR", brand="Ralph Lauren", size=groesse, condition="Sehr gut",
        photo_url=None, seller="wer", seller_url=None, favourites=0, views=0,
        posted_ts=None,
    )


async def _nichts(*args):
    return None


class GradedOrderTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor(
            SimpleNamespace(dedupe_scope="all"), db=None, client=None,
            on_items=_nichts, on_trouble=_nichts, on_recovered=_nichts,
        )
        self.monitor.profiles = load_profiles(BEISPIEL)

    def test_gruen_vor_gelb_auch_bei_kleinerer_marge(self):
        # Gelb mit 9 € Artikel, Grün mit 8 € — Grün zuerst, obwohl
        # das Gelbe (XL) als Erstes in der Liste stand.
        gelb = fund(1, "Ralph Lauren Quarter Zip Navy", 6.0, groesse="XL")
        gruen = fund(2, "Ralph Lauren Quarter Zip Navy", 8.0)
        ergebnis = self.monitor._graded([gelb, gruen])
        self.assertEqual([i.id for i in ergebnis], ["2", "1"])
        self.assertEqual(ergebnis[0].verdict.grade, deals.GREEN)
        self.assertEqual(ergebnis[1].verdict.grade, deals.YELLOW)

    def test_innerhalb_einer_farbe_hoehere_marge_zuerst(self):
        billig = fund(1, "Ralph Lauren Quarter Zip Navy", 5.0)
        teurer = fund(2, "Ralph Lauren Quarter Zip Navy", 8.0)
        ergebnis = self.monitor._graded([teurer, billig])
        self.assertEqual([i.id for i in ergebnis], ["1", "2"])

    def test_rot_und_fremdes_fliegen_raus(self):
        rot = fund(1, "Ralph Lauren Quarter Zip Navy Fleck", 5.0)
        fremd = fund(2, "Nike Hoodie", 5.0)
        self.assertEqual(self.monitor._graded([rot, fremd]), [])

    def test_ohne_profile_bleibt_alles(self):
        self.monitor.profiles = []
        items = [fund(1, "x", 1.0), fund(2, "y", 2.0)]
        self.assertEqual(self.monitor._graded(items), items)


if __name__ == "__main__":
    unittest.main()
