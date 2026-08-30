"""Tests für die Panel-Anmeldung.

Das Panel steht öffentlich im Netz. Ein Fehler hier heißt nicht „Funktion
kaputt", sondern „jeder darf rein" — deshalb sind die Randfälle hier wichtiger
als anderswo.
"""

import time
import unittest

from vinted_sniper.panel import auth

PASSWORT = "ein-langes-geheimes-passwort"


class TokenTests(unittest.TestCase):
    def test_eigenes_token_wird_akzeptiert(self):
        self.assertTrue(auth.token_valid(PASSWORT, auth.make_token(PASSWORT)))

    def test_ohne_token(self):
        self.assertFalse(auth.token_valid(PASSWORT, None))
        self.assertFalse(auth.token_valid(PASSWORT, ""))

    def test_muell_wird_abgelehnt(self):
        for kaputt in ("unsinn", "123", "123.", ".abc", "abc.def"):
            self.assertFalse(auth.token_valid(PASSWORT, kaputt), kaputt)

    def test_gefaelschte_signatur(self):
        token = auth.make_token(PASSWORT)
        ablauf, _, _ = token.rpartition(".")
        self.assertFalse(auth.token_valid(PASSWORT, f"{ablauf}.{'0' * 64}"))

    def test_verlaengern_ohne_signatur_scheitert(self):
        # Wer die Ablaufzeit hochsetzt, ohne den Schlüssel zu kennen, kommt
        # nicht rein — die Signatur passt dann nicht mehr.
        token = auth.make_token(PASSWORT)
        _, _, signatur = token.rpartition(".")
        weit_in_der_zukunft = str(int(time.time()) + 10_000_000)
        self.assertFalse(auth.token_valid(PASSWORT, f"{weit_in_der_zukunft}.{signatur}"))

    def test_abgelaufenes_token(self):
        self.assertFalse(auth.token_valid(PASSWORT, auth.make_token(PASSWORT, ttl=-1)))

    def test_anderes_passwort_entwertet_alte_sitzungen(self):
        token = auth.make_token(PASSWORT)
        self.assertFalse(auth.token_valid("neues-passwort", token))

    def test_token_enthaelt_das_passwort_nicht(self):
        self.assertNotIn(PASSWORT, auth.make_token(PASSWORT))


class PasswordTests(unittest.TestCase):
    def test_richtiges_passwort(self):
        self.assertTrue(auth.password_matches(PASSWORT, PASSWORT))

    def test_falsches_passwort(self):
        self.assertFalse(auth.password_matches(PASSWORT, "falsch"))
        self.assertFalse(auth.password_matches(PASSWORT, PASSWORT + "x"))
        self.assertFalse(auth.password_matches(PASSWORT, PASSWORT[:-1]))

    def test_leeres_konfiguriertes_passwort_laesst_niemanden_rein(self):
        # Ohne gesetztes Passwort startet das Panel gar nicht erst. Selbst wenn
        # doch, darf eine leere Eingabe nicht durchkommen.
        self.assertFalse(auth.password_matches("", ""))
        self.assertFalse(auth.password_matches("", "irgendwas"))


if __name__ == "__main__":
    unittest.main()
