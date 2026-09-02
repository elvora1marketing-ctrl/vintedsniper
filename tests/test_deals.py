"""Tests für die Kaufbewertung.

Hier entscheidet sich, ob ein Alert Geld wert ist. Zwei Fehler sind teuer: ein
Fund, der Grün bekommt und beim Checkout zu teuer ist — und einer, der still
verworfen wird, obwohl er gepasst hätte.
"""

import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from vinted_sniper import deals
from vinted_sniper.profiles import InvalidProfileFile, load_profiles
from vinted_sniper.vinted.models import Item

BEISPIEL = Path(__file__).resolve().parent.parent / "profiles.example.toml"


def item(
    titel="Polo Ralph Lauren Quarter Zip Pullover Navy",
    preis=9.0,
    groesse="L",
    zustand="Sehr gut",
    marke="Ralph Lauren",
):
    return Item(
        id="1",
        host="www.vinted.de",
        title=titel,
        url="https://www.vinted.de/items/1",
        price=preis,
        total_price=preis,
        currency="EUR",
        brand=marke,
        size=groesse,
        condition=zustand,
        photo_url="https://x/p.jpg",
        seller="wer",
        seller_url=None,
        favourites=0,
        views=0,
        posted_ts=None,
    )


class CostsTests(unittest.TestCase):
    def test_gesamtkosten(self):
        # 9 € Artikel + 2,99 Versand + 0,70 fix + 5 % = 13,14 €
        kosten = deals.Costs()
        self.assertAlmostEqual(kosten.total(9.0), 13.14, places=2)

    def test_aufbereitung_schlaegt_durch(self):
        kosten = deals.Costs(refurb=1.50)
        self.assertAlmostEqual(kosten.total(9.0), 14.64, places=2)

    def test_reserve_ist_keine_ausgabe(self):
        # Die Reserve mindert den Gewinn, nicht den Einkaufspreis — sonst wäre
        # der ausgewiesene Gesamt-EK falsch.
        self.assertAlmostEqual(
            deals.Costs(reserve=99.0).total(9.0), deals.Costs(reserve=0.0).total(9.0)
        )


class SizeTests(unittest.TestCase):
    def test_gaengige_schreibweisen(self):
        for roh, erwartet in (
            ("L", "L"), ("l", "L"), ("M / 38", "M"), ("Größe XL", "XL"),
            ("XXL", "XXL"), (None, ""),
        ):
            self.assertEqual(deals.normalize_size(roh), erwartet, roh)

    def test_xl_wird_nicht_zu_l(self):
        self.assertEqual(deals.normalize_size("XL"), "XL")


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.zip_profil, self.knit_profil = load_profiles(BEISPIEL)

    def urteil(self, **kwargs):
        return deals.evaluate(item(**kwargs), self.zip_profil)

    # ------------------------------------------------------------ Zuordnung

    def test_fremdes_produkt_gehoert_nicht_zum_profil(self):
        # Kein abgelehnter Deal, sondern schlicht etwas anderes.
        self.assertIsNone(self.urteil(titel="Nike Air Max 90", marke="Nike"))

    def test_marke_muss_stimmen(self):
        self.assertIsNone(self.urteil(titel="H&M Quarter Zip Pullover", marke="H&M"))

    def test_produktart_muss_stimmen(self):
        self.assertIsNone(self.urteil(titel="Ralph Lauren Poloshirt Navy"))

    # --------------------------------------------------------------- Rot

    def test_ausschlusswort_im_titel(self):
        u = self.urteil(titel="Ralph Lauren Quarter Zip Navy — kleiner Fleck")
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("fleck", u.notes[0])

    def test_damenware_faellt_raus(self):
        u = self.urteil(titel="Ralph Lauren Quarter Zip Damen Navy")
        self.assertEqual(u.grade, deals.RED)

    def test_falsche_groesse(self):
        self.assertEqual(self.urteil(groesse="S").grade, deals.RED)

    def test_zu_teuer_im_artikelpreis(self):
        self.assertEqual(self.urteil(preis=12.0).grade, deals.RED)

    def test_zustand_passt_nicht(self):
        self.assertEqual(self.urteil(zustand="Zufriedenstellend").grade, deals.RED)

    def test_unbekannter_zustand_wird_nicht_durchgewunken(self):
        u = self.urteil(zustand=None)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("unbekannt", u.notes[0])

    def test_marge_zu_klein(self):
        # 10 € Artikel → 14,20 € EK → 31 − 14,20 − 3 = 13,80 € … das reicht.
        # Bei einem VK von 25 € wäre es zu wenig.
        knapp = deals.Profile(
            name="knapp", match_any=("quarter zip",), resale_price=25.0,
            max_item_price=10.0,
        )
        u = deals.evaluate(item(preis=10.0), knapp)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("Marge", u.notes[0])

    def test_checkout_grenze_greift(self):
        # Artikelpreis unter der Grenze, Gesamtkosten aber darüber: teurer
        # Versand macht aus einem scheinbaren Schnäppchen einen Fehlkauf.
        teuer = deals.Profile(
            name="teurer Versand",
            match_any=("quarter zip",),
            resale_price=31.0,
            max_item_price=10.0,
            max_total_cost=15.0,
            costs=deals.Costs(shipping=6.0),
        )
        u = deals.evaluate(item(preis=9.0), teuer)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("Gesamt-EK", u.notes[0])

    # -------------------------------------------------------------- Grün

    def test_der_ideale_fund(self):
        u = self.urteil(preis=6.0)
        self.assertEqual(u.grade, deals.GREEN)
        self.assertEqual(u.notes, ())
        self.assertGreater(u.profit, 15.0)
        self.assertGreater(u.roi, 60.0)

    def test_kopfzeile_nennt_gewinn_und_einsatz(self):
        text = self.urteil(preis=6.0).headline()
        self.assertIn("GRÜN", text)
        self.assertIn("Gewinn", text)
        self.assertIn("ROI", text)

    # -------------------------------------------------------------- Gelb

    def test_xl_ist_gelb(self):
        u = self.urteil(preis=6.0, groesse="XL")
        self.assertEqual(u.grade, deals.YELLOW)
        self.assertIn("Größe XL", u.notes)

    def test_zustand_gut_ist_gelb(self):
        u = self.urteil(preis=6.0, zustand="Gut")
        self.assertEqual(u.grade, deals.YELLOW)

    def test_farblose_titel_sind_gelb(self):
        u = self.urteil(preis=6.0, titel="Ralph Lauren Quarter Zip Pullover")
        self.assertEqual(u.grade, deals.YELLOW)
        self.assertIn("Farbe steht nicht im Titel", u.notes)

    def test_knappe_marge_ist_gelb(self):
        u = self.urteil(preis=10.0)
        self.assertEqual(u.grade, deals.YELLOW)
        self.assertTrue(u.accepted)

    # ------------------------------------------------------- mehrere Profile

    def test_bestes_profil_gewinnt(self):
        # Ein Zopfmuster mit Viertelreißverschluss passt auf beide Profile.
        # Gelten soll das mit der besseren Ampel.
        strick = item(
            titel="Ralph Lauren Cable Knit Quarter Zip Pullover Navy", preis=12.0
        )
        u = deals.best_verdict(strick, [self.zip_profil, self.knit_profil])
        self.assertEqual(u.profile.name, "RL Cable Knit")
        self.assertEqual(u.grade, deals.GREEN)

    def test_ohne_passendes_profil_kein_urteil(self):
        nike = item(titel="Nike Hoodie", marke="Nike")
        self.assertIsNone(deals.best_verdict(nike, [self.zip_profil]))


class EbayTests(unittest.TestCase):
    def test_link_sucht_verkaufte_artikel(self):
        url = deals.ebay_sold_url(item())
        self.assertIn("LH_Sold=1", url)
        self.assertIn("LH_Complete=1", url)
        self.assertIn("Quarter", url)


class ProfileFileTests(unittest.TestCase):
    def schreiben(self, inhalt: str) -> Path:
        handle = NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
        handle.write(inhalt)
        handle.close()
        return Path(handle.name)

    def test_beispieldatei_laedt(self):
        profile = load_profiles(BEISPIEL)
        self.assertEqual(len(profile), 2)
        self.assertEqual(profile[0].max_total_cost, 15.0)
        self.assertEqual(profile[1].max_total_cost, 22.0)

    def test_fehlende_datei_ist_kein_fehler(self):
        # Ohne Profile meldet der Sniper wie bisher jeden Treffer.
        self.assertEqual(load_profiles(Path("/gibt/es/nicht.toml")), [])

    def test_kaputtes_toml_wird_gemeldet(self):
        with self.assertRaises(InvalidProfileFile):
            load_profiles(self.schreiben("[[profile]\nname = 'x'"))

    def test_fehlender_verkaufspreis(self):
        # Ohne erwarteten Verkaufspreis lässt sich keine Marge rechnen — das
        # darf nicht stillschweigend als 0 durchgehen.
        with self.assertRaises(InvalidProfileFile) as fehler:
            load_profiles(self.schreiben('[[profile]]\nname = "x"\n'))
        self.assertIn("resale_price", str(fehler.exception))

    def test_doppelter_name(self):
        with self.assertRaises(InvalidProfileFile):
            load_profiles(
                self.schreiben(
                    '[[profile]]\nname = "x"\nresale_price = 10\n'
                    '[[profile]]\nname = "x"\nresale_price = 10\n'
                )
            )

    def test_einfache_klammern_werden_erklaert(self):
        with self.assertRaises(InvalidProfileFile) as fehler:
            load_profiles(self.schreiben('[profile]\nname = "x"\nresale_price = 10\n'))
        self.assertIn("[[profile]]", str(fehler.exception))

    def test_globale_kosten_gelten_fuer_alle_profile(self):
        profile = load_profiles(
            self.schreiben(
                "[costs]\nshipping = 4.5\n\n"
                '[[profile]]\nname = "x"\nresale_price = 30\n'
            )
        )
        self.assertEqual(profile[0].costs.shipping, 4.5)

    def test_profil_darf_eigene_kosten_setzen(self):
        profile = load_profiles(
            self.schreiben(
                "[costs]\nshipping = 4.5\n\n"
                '[[profile]]\nname = "x"\nresale_price = 30\n'
                "[profile.costs]\nshipping = 1.0\n"
            )
        )
        self.assertEqual(profile[0].costs.shipping, 1.0)
        # Nicht überschriebene Werte kommen weiter aus dem globalen Block.
        self.assertEqual(profile[0].costs.reserve, 3.0)


class MaxBuyPriceTests(unittest.TestCase):
    """Die Umkehrung der Gewinnrechnung — „bis hierhin und keinen Euro weiter"."""

    def setUp(self):
        self.zip_profil, self.knit_profil = load_profiles(BEISPIEL)

    def test_bei_der_grenze_stimmt_die_marge_noch(self):
        grenze = deals.max_buy_price(self.zip_profil)
        urteil = deals.evaluate(item(preis=grenze), self.zip_profil)
        self.assertTrue(urteil.accepted)
        self.assertGreaterEqual(
            urteil.profit, self.zip_profil.thresholds.min_profit - 0.01
        )

    def test_einen_euro_darueber_nicht_mehr(self):
        grenze = deals.max_buy_price(self.zip_profil)
        urteil = deals.evaluate(item(preis=grenze + 1), self.zip_profil)
        self.assertEqual(urteil.grade, deals.RED)

    def test_die_engste_grenze_gewinnt(self):
        # Aus der Marge kämen hier gut 12 € heraus, `max_item_price` deckelt
        # aber bei 10 — die kleinere Zahl muss gelten.
        self.assertLessEqual(
            deals.max_buy_price(self.zip_profil), self.zip_profil.max_item_price
        )

    def test_teurer_versand_senkt_die_grenze(self):
        guenstig = deals.Profile(
            name="a", match_any=("zip",), resale_price=31.0,
            costs=deals.Costs(shipping=2.99),
        )
        teuer = deals.Profile(
            name="b", match_any=("zip",), resale_price=31.0,
            costs=deals.Costs(shipping=6.99),
        )
        self.assertLess(deals.max_buy_price(teuer), deals.max_buy_price(guenstig))

    def test_nie_negativ(self):
        # Ein Verkaufspreis unter den Fixkosten darf keine negative Grenze
        # ergeben — „kauf für minus drei Euro" ist keine Aussage.
        aussichtslos = deals.Profile(
            name="x", match_any=("zip",), resale_price=5.0
        )
        self.assertEqual(deals.max_buy_price(aussichtslos), 0.0)


if __name__ == "__main__":
    unittest.main()


class BillTests(unittest.TestCase):
    """Die Rechnung hinter dem Urteil — jeder Posten nachvollziehbar."""

    def setUp(self):
        self.zip_profil, _ = load_profiles(BEISPIEL)

    def test_cent_rundung_kaufmaennisch(self):
        self.assertEqual(deals.cents(1.005), 1.01)
        self.assertEqual(deals.cents(1.004), 1.0)
        self.assertEqual(deals.cents(11.999999), 12.0)

    def test_geschaetzt_wenn_vinted_nichts_liefert(self):
        fund = Item(**{**item(preis=9.0).__dict__, "total_price": None})
        u = deals.evaluate(fund, self.zip_profil)
        self.assertEqual(u.bill.source, deals.ESTIMATED)
        # 0,70 + 5 % von 9 = 1,15
        self.assertEqual(u.bill.protection, 1.15)
        self.assertEqual(u.bill.shipping, 2.99)
        self.assertEqual(u.total_cost, 13.14)
        self.assertEqual(u.profit, 14.86)

    def test_kaeuferschutz_von_vinted_schlaegt_die_schaetzung(self):
        # Vinted liefert 9,00 → 10,10 inkl. Käuferschutz: dann sind es 1,10,
        # nicht die geschätzten 1,15.
        fund = Item(**{**item(preis=9.0).__dict__, "total_price": 10.10})
        u = deals.evaluate(fund, self.zip_profil)
        self.assertEqual(u.bill.source, deals.FROM_VINTED)
        self.assertEqual(u.bill.protection, 1.10)
        self.assertEqual(u.total_cost, 9.0 + 2.99 + 1.10)

    def test_checkout_betrag_ersetzt_alles(self):
        fund = item(preis=9.0)
        u = deals.evaluate(fund, self.zip_profil, checkout_total=14.49)
        self.assertEqual(u.bill.source, deals.FROM_CHECKOUT)
        self.assertEqual(u.total_cost, 14.49)
        self.assertEqual(u.profit, 13.51)
        self.assertIn("laut Checkout", u.breakdown())

    def test_checkout_unter_artikelpreis_ist_unsinn(self):
        u = deals.evaluate(item(preis=9.0), self.zip_profil, checkout_total=5.0)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("Checkout", u.notes[0])

    def test_rechnung_steht_im_alert(self):
        text = deals.evaluate(item(preis=9.0), self.zip_profil).breakdown()
        self.assertIn("9,00 € Artikel", text)
        self.assertIn("2,99 € Versand", text)
        self.assertIn("Gesamt-EK", text)
        self.assertIn("31,00 € VK", text)
        self.assertIn("Reserve", text)
        self.assertIn("Gewinn", text)
        self.assertIn("ROI", text)

    def test_rot_vor_der_rechnung_hat_keine_rechnung(self):
        u = deals.evaluate(item(groesse="S"), self.zip_profil)
        self.assertIsNone(u.bill)
        self.assertEqual(u.breakdown(), "")

    def test_rot_nach_der_rechnung_zeigt_sie_trotzdem(self):
        # Wer sehen will, warum es nicht reicht, braucht die Zahlen.
        knapp = deals.Profile(
            name="knapp", match_any=("quarter zip",), resale_price=20.0
        )
        u = deals.evaluate(item(preis=9.0), knapp)
        self.assertEqual(u.grade, deals.RED)
        self.assertIsNotNone(u.bill)
        self.assertIn("Gewinn", u.breakdown())

    def test_verlust_wird_mit_minus_gezeigt(self):
        verlust = deals.Profile(name="v", match_any=("quarter zip",), resale_price=5.0)
        u = deals.evaluate(item(preis=9.0), verlust)
        self.assertLess(u.profit, 0)
        self.assertIn("−", u.headline())
        self.assertIn("−", u.breakdown())


class CurrencyTests(unittest.TestCase):
    """Funde in Fremdwährung werden umgerechnet — oder ehrlich abgelehnt."""

    def setUp(self):
        self.zip_profil, _ = load_profiles(BEISPIEL)

    def fund(self, preis, waehrung):
        return Item(**{**item(preis=preis).__dict__, "currency": waehrung,
                       "total_price": None})

    def test_ohne_kurs_kein_raten(self):
        ohne = deals.Profile(
            name="x", match_any=("quarter zip",), resale_price=31.0, rates={}
        )
        u = deals.evaluate(self.fund(8.0, "GBP"), ohne)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("GBP", u.notes[0])
        self.assertIn("Kurs", u.notes[0])

    def test_mit_kurs_wird_umgerechnet(self):
        u = deals.evaluate(self.fund(8.0, "GBP"), self.zip_profil)
        self.assertEqual(u.bill.converted, (8.0, "GBP", 1.17))
        self.assertEqual(u.bill.item_price, 9.36)
        self.assertIn("8,00 GBP × 1.17", u.breakdown())
        self.assertTrue(u.accepted)

    def test_meldegrenze_gilt_nach_umrechnung(self):
        # 9 GBP sind 10,53 € — über der 10-€-Grenze, auch wenn 9 < 10.
        u = deals.evaluate(self.fund(9.0, "GBP"), self.zip_profil)
        self.assertEqual(u.grade, deals.RED)
        self.assertIn("Meldegrenze", u.notes[0])

    def test_profilwaehrung_braucht_keinen_kurs(self):
        u = deals.evaluate(self.fund(9.0, "eur"), self.zip_profil)
        self.assertIsNone(u.bill.converted)
        self.assertTrue(u.accepted)

    def test_kurse_aus_der_datei(self):
        profile = load_profiles(BEISPIEL)
        self.assertEqual(profile[0].currency, "EUR")
        self.assertEqual(profile[0].rates["GBP"], 1.17)
        self.assertNotIn("EUR", profile[0].rates)


class RatesFileTests(ProfileFileTests):
    def test_kurs_null_wird_abgelehnt(self):
        with self.assertRaises(InvalidProfileFile):
            load_profiles(self.schreiben(
                '[rates]\nGBP = 0\n[[profile]]\nname = "x"\nresale_price = 10\n'
            ))

    def test_kaputte_waehrung(self):
        with self.assertRaises(InvalidProfileFile):
            load_profiles(self.schreiben(
                'currency = "Euro"\n[[profile]]\nname = "x"\nresale_price = 10\n'
            ))

    def test_eigene_waehrung(self):
        profile = load_profiles(self.schreiben(
            'currency = "gbp"\n[rates]\nEUR = 0.85\n'
            '[[profile]]\nname = "x"\nresale_price = 10\n'
        ))
        self.assertEqual(profile[0].currency, "GBP")
        self.assertEqual(profile[0].rates, {"EUR": 0.85})


class MaxBuyPriceExactTests(unittest.TestCase):
    def test_grenze_ist_auf_den_cent_genau(self):
        # Ohne Deckel bindet die Marge: bei der Grenze ≥ 12 €, einen Cent
        # darüber nicht mehr.
        frei = deals.Profile(name="frei", match_any=("quarter zip",), resale_price=31.0)
        grenze = deals.max_buy_price(frei)
        self.assertEqual(grenze, round(grenze, 2))
        bei = deals.evaluate(item(preis=grenze), frei)
        self.assertGreaterEqual(bei.profit, 12.0)
        darueber = deals.evaluate(item(preis=grenze + 0.01), frei)
        self.assertEqual(darueber.grade, deals.RED)
