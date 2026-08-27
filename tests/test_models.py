"""Tests für das Item-Parsing — die API-Antwort ist nicht überall gleich befüllt."""

import time
import unittest

from vinted_sniper.vinted.models import Item

FULL_PAYLOAD = {
    "id": 4711,
    "title": "Nike Air Max 90",
    "url": "https://www.vinted.de/items/4711-nike-air-max-90",
    "price": {"amount": "45.0", "currency_code": "EUR"},
    "total_item_price": {"amount": "48.70", "currency_code": "EUR"},
    "brand_title": "Nike",
    "size_title": "42",
    "status": "Sehr gut",
    "photo": {
        "url": "https://images.vinted.net/small.jpg",
        "high_resolution": {
            "url": "https://images.vinted.net/large.jpg",
            "timestamp": 1_700_000_000,
        },
    },
    "user": {"id": 9, "login": "sneakerfan", "profile_url": "https://www.vinted.de/member/9"},
    "favourite_count": 3,
    "view_count": 21,
}


class ItemParseTests(unittest.TestCase):
    def test_vollstaendige_antwort(self):
        item = Item.parse(FULL_PAYLOAD, "www.vinted.de", "EUR")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.id, "4711")
        self.assertEqual(item.title, "Nike Air Max 90")
        self.assertEqual(item.price, 45.0)
        self.assertEqual(item.total_price, 48.70)
        self.assertEqual(item.currency, "EUR")
        self.assertEqual(item.brand, "Nike")
        self.assertEqual(item.size, "42")
        self.assertEqual(item.seller, "sneakerfan")
        self.assertEqual(item.photo_url, "https://images.vinted.net/large.jpg")
        self.assertEqual(item.posted_ts, 1_700_000_000)

    def test_item_ohne_id_wird_verworfen(self):
        self.assertIsNone(Item.parse({"title": "kaputt"}, "www.vinted.de", "EUR"))

    def test_minimale_antwort_kippt_nicht_um(self):
        item = Item.parse({"id": 1}, "www.vinted.de", "EUR")
        assert item is not None
        self.assertEqual(item.title, "Ohne Titel")
        self.assertIsNone(item.price)
        self.assertIsNone(item.brand)
        self.assertIsNone(item.photo_url)
        self.assertIsNone(item.posted_ts)
        # Ohne `url` in der Antwort bauen wir den Link selbst.
        self.assertEqual(item.url, "https://www.vinted.de/items/1")

    def test_kaputter_preis_wird_zu_none_statt_zu_einem_absturz(self):
        item = Item.parse(
            {"id": 2, "price": {"amount": "n/a", "currency_code": "EUR"}},
            "www.vinted.de",
            "EUR",
        )
        assert item is not None
        self.assertIsNone(item.price)
        self.assertEqual(item.currency, "EUR")
        self.assertEqual(item.price_label(), "Preis unbekannt")

    def test_waehrung_faellt_auf_domain_zurueck(self):
        item = Item.parse({"id": 3, "price": 12.5}, "www.vinted.pl", "PLN")
        assert item is not None
        self.assertEqual(item.currency, "PLN")

    def test_zeitstempel_als_string(self):
        item = Item.parse(
            {"id": 4, "photo": {"high_resolution": {"timestamp": "1700000000"}}},
            "www.vinted.de",
            "EUR",
        )
        assert item is not None
        self.assertEqual(item.posted_ts, 1_700_000_000)

    def test_created_at_ts_als_ausweichfeld(self):
        item = Item.parse({"id": 5, "created_at_ts": 1_700_000_001}, "www.vinted.de", "EUR")
        assert item is not None
        self.assertEqual(item.posted_ts, 1_700_000_001)


class ItemDisplayTests(unittest.TestCase):
    def test_preislabel_zeigt_kaeuferschutz_nur_bei_aufschlag(self):
        item = Item.parse(FULL_PAYLOAD, "www.vinted.de", "EUR")
        assert item is not None
        self.assertIn("45,00 EUR", item.price_label())
        self.assertIn("48,70", item.price_label())

        ohne_aufschlag = Item.parse(
            {**FULL_PAYLOAD, "total_item_price": {"amount": "45.0", "currency_code": "EUR"}},
            "www.vinted.de",
            "EUR",
        )
        assert ohne_aufschlag is not None
        self.assertNotIn("Schutz", ohne_aufschlag.price_label())

    def test_kauflink_zeigt_auf_dieselbe_domain(self):
        item = Item.parse(FULL_PAYLOAD, "www.vinted.fr", "EUR")
        assert item is not None
        self.assertTrue(item.buy_url.startswith("https://www.vinted.fr/transaction/buy/new"))
        self.assertIn("4711", item.buy_url)

    def test_alter_wird_aus_dem_zeitstempel_berechnet(self):
        recent = Item.parse(
            {"id": 6, "photo": {"high_resolution": {"timestamp": int(time.time()) - 120}}},
            "www.vinted.de",
            "EUR",
        )
        assert recent is not None
        age = recent.age_seconds
        self.assertIsNotNone(age)
        assert age is not None
        self.assertGreaterEqual(age, 119)
        self.assertLessEqual(age, 130)

    def test_alter_ohne_zeitstempel_ist_unbekannt(self):
        item = Item.parse({"id": 7}, "www.vinted.de", "EUR")
        assert item is not None
        self.assertIsNone(item.age_seconds)


if __name__ == "__main__":
    unittest.main()
