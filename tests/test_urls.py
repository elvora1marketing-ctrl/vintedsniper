"""Tests für den Such-URL-Parser — die Stelle, an der Nutzereingaben ankommen."""

import unittest

from vinted_sniper.vinted import domains
from vinted_sniper.vinted.urls import InvalidSearchURL, parse_search_url


class ParseSearchURLTests(unittest.TestCase):
    def test_suchtext_und_preisfilter(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?search_text=nike%20air%20max"
            "&price_from=10&price_to=50&currency=EUR"
        )
        self.assertEqual(query.host, "www.vinted.de")
        self.assertEqual(query.scalars["search_text"], "nike air max")
        self.assertEqual(query.scalars["price_from"], "10")
        self.assertEqual(query.scalars["price_to"], "50")

    def test_listen_als_klammer_syntax(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?brand_ids[]=53&brand_ids[]=14&size_ids[]=207"
        )
        self.assertEqual(query.lists["brand_ids"], ["53", "14"])
        self.assertEqual(query.lists["size_ids"], ["207"])

    def test_listen_als_kommaliste(self):
        query = parse_search_url("https://www.vinted.de/catalog?brand_ids=53,14")
        self.assertEqual(query.lists["brand_ids"], ["53", "14"])

    def test_duplikate_werden_zusammengefasst(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?brand_ids[]=53&brand_ids[]=53"
        )
        self.assertEqual(query.lists["brand_ids"], ["53"])

    def test_katalog_id_aus_pfad(self):
        query = parse_search_url("https://www.vinted.de/catalog/1904-t-shirts?search_text=polo")
        self.assertEqual(query.lists["catalog_ids"], ["1904"])
        self.assertEqual(query.scalars["search_text"], "polo")

    def test_website_alias_catalog_wird_gemappt(self):
        query = parse_search_url("https://www.vinted.fr/catalog?catalog[]=1904&price_max=30")
        self.assertEqual(query.lists["catalog_ids"], ["1904"])
        self.assertEqual(query.scalars["price_to"], "30")

    def test_unbekannte_parameter_fliegen_raus(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?search_text=jacke&utm_source=mail&time=123"
        )
        self.assertEqual(query.scalars, {"search_text": "jacke"})
        self.assertEqual(query.lists, {})

    def test_host_normalisierung_und_schema_ergaenzung(self):
        query = parse_search_url("vinted.pl/catalog?search_text=buty")
        self.assertEqual(query.host, "www.vinted.pl")
        self.assertEqual(query.domain.currency, "PLN")

    def test_andere_laenderdomains(self):
        for raw_host, expected_currency in [
            ("www.vinted.co.uk", "GBP"),
            ("www.vinted.fr", "EUR"),
            ("www.vinted.cz", "CZK"),
            ("www.vinted.se", "SEK"),
        ]:
            query = parse_search_url(f"https://{raw_host}/catalog?search_text=test")
            self.assertEqual(query.domain.currency, expected_currency, raw_host)

    def test_unbekannte_vinted_tld_faellt_auf_eur_zurueck(self):
        query = parse_search_url("https://www.vinted.xx/catalog?search_text=test")
        self.assertEqual(query.host, "www.vinted.xx")
        self.assertEqual(query.domain.currency, "EUR")

    def test_fremde_domain_wird_abgelehnt(self):
        with self.assertRaises(InvalidSearchURL):
            parse_search_url("https://www.ebay.de/sch/i.html?_nkw=nike")

    def test_leere_eingabe_wird_abgelehnt(self):
        with self.assertRaises(InvalidSearchURL):
            parse_search_url("   ")

    def test_url_ganz_ohne_filter_wird_abgelehnt(self):
        with self.assertRaises(InvalidSearchURL):
            parse_search_url("https://www.vinted.de/catalog")

    def test_discord_spitzklammern_werden_entfernt(self):
        query = parse_search_url("<https://www.vinted.de/catalog?search_text=nike>")
        self.assertEqual(query.scalars["search_text"], "nike")


class ApiParamsTests(unittest.TestCase):
    def test_listen_werden_komma_separiert(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?brand_ids[]=53&brand_ids[]=14&search_text=nike"
        )
        params = query.api_params(page=1, per_page=20)
        self.assertEqual(params["brand_ids"], "53,14")
        self.assertEqual(params["search_text"], "nike")
        self.assertEqual(params["page"], "1")
        self.assertEqual(params["per_page"], "20")

    def test_sortierung_wird_immer_erzwungen(self):
        # Ein Sniper darf nie nach Relevanz sortieren, sonst entgehen ihm neue Artikel.
        query = parse_search_url(
            "https://www.vinted.de/catalog?search_text=nike&order=price_low_to_high"
        )
        self.assertEqual(query.api_params(page=1, per_page=20)["order"], "newest_first")

    def test_waehrung_wird_aus_domain_ergaenzt(self):
        query = parse_search_url("https://www.vinted.co.uk/catalog?search_text=nike")
        self.assertEqual(query.api_params(page=1, per_page=20)["currency"], "GBP")

    def test_explizite_waehrung_bleibt_erhalten(self):
        query = parse_search_url(
            "https://www.vinted.co.uk/catalog?search_text=nike&currency=EUR"
        )
        self.assertEqual(query.api_params(page=1, per_page=20)["currency"], "EUR")


class RoundTripTests(unittest.TestCase):
    def test_web_url_ist_wieder_parsebar(self):
        original = parse_search_url(
            "https://www.vinted.de/catalog?search_text=nike%20air&brand_ids[]=53"
            "&brand_ids[]=14&price_to=50"
        )
        reparsed = parse_search_url(original.web_url())
        self.assertEqual(reparsed.host, original.host)
        self.assertEqual(reparsed.lists, original.lists)
        self.assertEqual(reparsed.scalars, original.scalars)

    def test_beschreibung_nennt_die_wesentlichen_filter(self):
        query = parse_search_url(
            "https://www.vinted.de/catalog?search_text=nike&price_to=50&brand_ids[]=53"
        )
        description = query.describe()
        self.assertIn("nike", description)
        self.assertIn("50", description)
        self.assertIn("Marken", description)


class DomainTests(unittest.TestCase):
    def test_erkennung_von_vinted_hosts(self):
        self.assertTrue(domains.is_vinted_host("vinted.de"))
        self.assertTrue(domains.is_vinted_host("www.vinted.co.uk"))
        self.assertTrue(domains.is_vinted_host("www.vinted.com"))
        self.assertTrue(domains.is_vinted_host("www.vinted.xyz"))
        self.assertFalse(domains.is_vinted_host("ebay.de"))

    def test_host_darf_nicht_untergeschoben_werden(self):
        # Ein Substring-Check würde diese Hosts durchwinken und den Bot seine
        # Requests an einen fremden Server schicken lassen.
        for host in (
            "www.vinted.de.angreifer.com",
            "vinted.de.angreifer.com",
            "vinted.evil.com",
            "meine-vinted.de-shop.com",
            "www.vinted.de.co",
        ):
            self.assertFalse(domains.is_vinted_host(host), host)
            with self.assertRaises(InvalidSearchURL, msg=host):
                parse_search_url(f"https://{host}/catalog?search_text=nike")

    def test_normalisierung(self):
        self.assertEqual(domains.normalize_host("vinted.de"), "www.vinted.de")
        self.assertEqual(domains.normalize_host("https://vinted.de/"), "www.vinted.de")
        self.assertEqual(domains.normalize_host("WWW.VINTED.DE"), "www.vinted.de")


if __name__ == "__main__":
    unittest.main()
