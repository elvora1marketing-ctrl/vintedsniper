"""Anmeldung fürs Panel — signiertes Cookie statt Basic Auth.

Basic Auth wäre weniger Code, aber der Browser hängt die Zugangsdaten dann an
*jede* Anfrage an die Domain — auch an eine, die eine fremde Seite auslöst.
Ein Formular-Login mit `SameSite=Strict`-Cookie schließt das aus: der Browser
schickt es nur mit, wenn die Anfrage von der Seite selbst kommt.

Das Cookie enthält keine Geheimnisse, nur ein Ablaufdatum und dessen Signatur.
Der Schlüssel leitet sich aus dem Passwort ab — wer es ändert, macht damit
automatisch alle offenen Sitzungen ungültig.
"""

from __future__ import annotations

import hashlib
import hmac
import time

COOKIE_NAME = "vs_session"
# Eine Woche: lang genug, um nicht zu nerven, kurz genug, dass ein vergessenes
# Gerät irgendwann von selbst zufällt.
SESSION_TTL = 7 * 24 * 3600


def _key(password: str) -> bytes:
    return hashlib.sha256(f"vinted-sniper-panel::{password}".encode()).digest()


def make_token(password: str, *, ttl: int = SESSION_TTL) -> str:
    expires = str(int(time.time()) + ttl)
    signature = hmac.new(_key(password), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def token_valid(password: str, token: str | None) -> bool:
    if not token:
        return False
    expires, _, signature = token.rpartition(".")
    if not expires or not signature:
        return False

    expected = hmac.new(_key(password), expires.encode(), hashlib.sha256).hexdigest()
    # Zeitkonstanter Vergleich: ein normales == verriete über die Laufzeit,
    # wie viele Zeichen schon stimmen.
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        return int(expires) > time.time()
    except ValueError:
        return False


def password_matches(configured: str, entered: str) -> bool:
    return bool(configured) and hmac.compare_digest(configured, entered)
