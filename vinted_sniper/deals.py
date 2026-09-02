"""Bewertung eines Fundes nach Marge, nicht nach Neuheit.

Ein Sniper, der „günstige Ralph-Lauren-Sachen" meldet, ist nutzlos: 10 € Preis
plus Versand plus Käuferschutz können bereits zu teuer sein. Was zählt, ist der
Betrag, der nach allen Kosten übrig bleibt.

Dieses Modul rechnet genau das aus und vergibt eine Ampel:

* 🟢 **grün** — deutlich über der Gewinnschwelle, gefragte Größe und Farbe,
  bester Zustand. Der Fund, für den man alles stehen lässt.
* 🟡 **gelb** — kaufbar, aber erst nachfragen: XL, Zustand „gut", Farbe steht
  nicht im Titel, Marge nur knapp über der Schwelle.
* 🔴 **rot** — unter der Gewinnschwelle, falsche Größe, ausgeschlossene Wörter
  im Titel. Kommt gar nicht erst in den Channel.

**Was die Note nicht kann.** Sie beurteilt, was in den Angebotsdaten steht:
Preis, Größe, Zustand, Titel. Echtheit, Pflegeetikett, Löcher, Flecken, ein
funktionierender Reißverschluss und die Frage, ob ein Verkäufer zehn angeblich
neue Ralph-Lauren-Teile auf einmal anbietet — das sieht nur ein Mensch auf den
Fotos. Deshalb hängt an jedem Alert die Prüfliste; die Note sagt „hinschauen
lohnt sich", nicht „kaufen".

Der Sniper kauft nichts und wird nichts kaufen. Er meldet.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping
from urllib.parse import quote_plus

from .vinted.models import Item


def cents(value: float) -> float:
    """Kaufmännisch auf den Cent runden.

    Geld wird hier grundsätzlich in gerundeten Beträgen verglichen. Sonst
    entscheidet irgendwann ein 11,999999 € gegen 12 € — und niemand könnte
    nachrechnen, warum ein Fund Rot bekam.
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Costs:
    """Was ein Artikel am Ende wirklich kostet.

    Die Voreinstellungen sind Schätzwerte für einen deutschen Standardversand.
    Käuferschutz und Versand ändert Vinted von Zeit zu Zeit und je nach Land —
    die Zahlen gehören einmal am echten Kaufabschluss gegengeprüft und dann in
    die `profiles.toml`.

    Wo Vinted den Käuferschutz selbst mitliefert (`total_item_price` in den
    Angebotsdaten), wird der genommen und nicht die Schätzung — siehe
    `evaluate`.
    """

    shipping: float = 2.99
    protection_fixed: float = 0.70
    protection_percent: float = 5.0
    # Waschen, Dämpfen, Fusseln — pro Stück gerechnet.
    refurb: float = 0.0
    # Puffer für Preisnachlässe und kleine Überraschungen. Wird vom Gewinn
    # abgezogen, nicht auf die Kosten geschlagen: er ist Risiko, nicht Ausgabe.
    reserve: float = 3.0

    def protection(self, price: float) -> float:
        """Geschätzter Käuferschutz für einen Artikelpreis."""
        return cents(self.protection_fixed + price * self.protection_percent / 100.0)

    def total(self, price: float) -> float:
        """Einkaufspreis inklusive allem, was oben draufkommt."""
        return cents(price + self.shipping + self.protection(price) + self.refurb)


# Woher die Nebenkosten einer Rechnung stammen. Steht im Alert, damit man
# weiß, wie belastbar die Zahl ist.
ESTIMATED = "geschaetzt"
FROM_VINTED = "vinted"
FROM_CHECKOUT = "checkout"


@dataclass(frozen=True)
class Bill:
    """Die Rechnung hinter einem Urteil — jeder Posten einzeln.

    Ein Gewinn ohne Rechnung ist eine Behauptung. Hier steht, was wie
    zusammengezählt wurde, in der Währung des Profils.
    """

    item_price: float
    shipping: float
    protection: float
    refurb: float
    reserve: float
    resale_price: float
    currency: str = "EUR"
    # ESTIMATED: Versand und Käuferschutz aus dem Profil geschätzt.
    # FROM_VINTED: Käuferschutz aus den Angebotsdaten, Versand geschätzt.
    # FROM_CHECKOUT: Gesamtbetrag aus dem Checkout abgelesen; Versand und
    # Käuferschutz stehen dann zusammen in `protection`.
    source: str = ESTIMATED
    # (Originalpreis, Originalwährung, Kurs), wenn umgerechnet wurde.
    converted: tuple[float, str, float] | None = None

    @property
    def total(self) -> float:
        return cents(self.item_price + self.shipping + self.protection + self.refurb)

    @property
    def profit(self) -> float:
        return cents(self.resale_price - self.total - self.reserve)

    @property
    def roi(self) -> float:
        """Gewinn im Verhältnis zum eingesetzten Geld, in Prozent."""
        return (self.profit / self.total * 100.0) if self.total > 0 else 0.0

    def _geld(self, value: float) -> str:
        betrag = f"{value:.2f}".replace(".", ",")
        return f"{betrag} €" if self.currency == "EUR" else f"{betrag} {self.currency}"

    def lines(self) -> tuple[str, str]:
        """Zwei Zeilen: Einkauf und Ergebnis — zum Nachrechnen im Alert."""
        posten = [f"{self._geld(self.item_price)} Artikel"]
        if self.converted is not None:
            original, waehrung, kurs = self.converted
            posten[0] = (
                f"{original:.2f} {waehrung} × {kurs:g} = "
                f"{self._geld(self.item_price)} Artikel"
            ).replace(".", ",", 1)
        if self.source == FROM_CHECKOUT:
            posten.append(
                f"{self._geld(self.protection)} Versand + Käuferschutz laut Checkout"
            )
        else:
            posten.append(f"{self._geld(self.shipping)} Versand (geschätzt)")
            quelle = "laut Vinted" if self.source == FROM_VINTED else "geschätzt"
            posten.append(f"{self._geld(self.protection)} Käuferschutz ({quelle})")
        if self.refurb:
            posten.append(f"{self._geld(self.refurb)} Aufbereitung")
        einkauf = " + ".join(posten) + f" = **{self._geld(self.total)} Gesamt-EK**"

        vorzeichen = "+" if self.profit >= 0 else "−"
        ergebnis = (
            f"{self._geld(self.resale_price)} VK − {self._geld(self.total)} − "
            f"{self._geld(self.reserve)} Reserve = **{vorzeichen}"
            f"{self._geld(abs(self.profit))} Gewinn** ({self.roi:.0f} % ROI)"
        )
        return einkauf, ergebnis


@dataclass(frozen=True)
class Thresholds:
    """Ab wann ein Fund kaufbar ist — und ab wann er richtig gut ist."""

    min_profit: float = 12.0
    min_roi: float = 60.0
    # Ab diesem Gewinn wird aus Gelb Grün, sofern auch der Rest stimmt.
    green_profit: float = 15.0


@dataclass(frozen=True)
class Profile:
    """Ein Produktzuschnitt: was gesucht wird und was es einbringt."""

    name: str
    # Mindestens eines dieser Wörter muss im Titel stehen.
    match_any: tuple[str, ...] = ()
    # Alle diese Wörter müssen im Titel stehen (z. B. die Marke).
    match_all: tuple[str, ...] = ()
    # Eines dieser Wörter genügt zum Aussortieren.
    exclude: tuple[str, ...] = ()
    # Konservativer Verkaufspreis. Konservativ heißt: der Preis, zu dem das
    # Teil sicher weggeht, nicht der Wunschpreis.
    resale_price: float = 0.0
    # Darüber wird gar nicht erst gerechnet.
    max_item_price: float = 0.0
    # Obergrenze für den geschätzten Gesamtpreis (Artikel + Versand +
    # Käuferschutz). Der Artikelpreis allein sagt wenig: 10 € plus 5 € Versand
    # sind teurer als 12 € plus Gratisversand.
    max_total_cost: float = 0.0
    # Leer = Größe egal.
    sizes: tuple[str, ...] = ()
    # Größen, die eine A-Note zulassen (die gefragten).
    top_sizes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    top_colors: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    top_conditions: tuple[str, ...] = ()
    costs: Costs = field(default_factory=Costs)
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Währung, in der Verkaufspreis und Kosten angegeben sind.
    currency: str = "EUR"
    # Kurse für Funde in anderen Währungen: Fremdwährung → Profilwährung,
    # z. B. {"GBP": 1.17}. Fehlt der Kurs, wird nicht geraten, sondern Rot.
    rates: Mapping[str, float] = field(default_factory=dict)


GREEN = "gruen"
YELLOW = "gelb"
RED = "rot"

_AMPEL = {GREEN: "🟢 GRÜN — kaufen", YELLOW: "🟡 GELB — erst nachfragen", RED: "🔴 ROT"}


def max_buy_price(profile: Profile) -> float:
    """Der höchste Artikelpreis, bei dem die Marge noch stimmt.

    Die Umkehrung der Gewinnrechnung — und die Zahl, die man beim Angebot
    tatsächlich braucht: „bis hierhin und keinen Euro weiter."

        VK − Reserve − Mindestgewinn ≥ Preis·(1 + Käuferschutz%) + Fixkosten

    Begrenzt zusätzlich durch die eingetragenen Obergrenzen für Artikelpreis
    und Gesamtkosten — was davon zuerst greift, gewinnt.
    """
    kosten = profile.costs
    grenzen = profile.thresholds

    fix = kosten.shipping + kosten.protection_fixed + kosten.refurb
    anteil = 1 + kosten.protection_percent / 100.0
    aus_marge = (
        profile.resale_price - kosten.reserve - grenzen.min_profit - fix
    ) / anteil

    kandidaten = [aus_marge]
    if profile.max_item_price:
        kandidaten.append(profile.max_item_price)
    if profile.max_total_cost:
        kandidaten.append((profile.max_total_cost - fix) / anteil)
    # Abrunden, nicht runden: „bis 9,54 €" muss die Marge noch halten, und
    # 9,545 aufgerundet täte das nicht.
    return max(0.0, math.floor(min(kandidaten) * 100.0 + 1e-9) / 100.0)


@dataclass(frozen=True)
class Verdict:
    """Das Urteil über einen Fund."""

    profile: Profile
    grade: str
    profit: float
    roi: float
    total_cost: float
    # Warum es nicht Grün wurde bzw. woran es scheitert.
    notes: tuple[str, ...] = ()
    # Die Rechnung dahinter. Fehlt, wenn der Fund vor dem Rechnen ausschied
    # (Ausschlusswort, Größe, Zustand).
    bill: Bill | None = None

    @property
    def accepted(self) -> bool:
        return self.grade in (GREEN, YELLOW)

    def headline(self) -> str:
        vorzeichen = "+" if self.profit >= 0 else "−"
        euro = f"{abs(self.profit):.2f}".replace(".", ",")
        kosten = f"{self.total_cost:.2f}".replace(".", ",")
        return (
            f"**{_AMPEL[self.grade]}** · {self.profile.name}\n"
            f"≈ **{vorzeichen}{euro} € Gewinn** bei {kosten} € Gesamt-EK · "
            f"{self.roi:.0f} % ROI"
        )

    def breakdown(self) -> str:
        """Die Rechnung zum Nachvollziehen, Zeile für Zeile."""
        if self.bill is None:
            return ""
        return "\n".join(self.bill.lines())


# Größenangaben kommen als „M", „m", „M / 38", „Größe L". Das Ziel ist, das
# eine Kürzel herauszuholen, ohne bei „XL" versehentlich „L" zu treffen.
_SIZE_TOKEN = re.compile(r"\b(xxs|xs|s|m|l|xl|xxl|xxxl)\b", re.IGNORECASE)


def normalize_size(raw: str | None) -> str:
    if not raw:
        return ""
    treffer = _SIZE_TOKEN.search(raw)
    return treffer.group(1).upper() if treffer else raw.strip().upper()


def _contains(haystack: str, needles: tuple[str, ...]) -> str | None:
    """Erstes gefundenes Stichwort zurückgeben."""
    for needle in needles:
        if needle and needle in haystack:
            return needle
    return None


def ebay_sold_url(item: Item) -> str:
    """Suche nach *tatsächlich verkauften* Artikeln auf eBay.

    Der Median der Vinted-Angebote sagt, was verlangt wird. Was bezahlt wurde,
    steht bei eBay unter „Verkaufte Artikel" — der einzige frei zugängliche
    Beleg dafür, dass ein Preis realistisch ist. Ein Klick statt Abtippen.
    """
    begriffe = " ".join(
        teil for teil in (item.brand, item.title) if teil
    )[:120]
    return (
        "https://www.ebay.de/sch/i.html?_nkw="
        f"{quote_plus(begriffe)}&LH_Sold=1&LH_Complete=1"
    )


def _bill(
    item: Item, profile: Profile, *, checkout_total: float | None
) -> tuple[Bill | None, str | None]:
    """Die Rechnung zu einem Fund aufstellen.

    Reihenfolge der Quellen, verlässlichste zuerst:

    1. `checkout_total` — der abgelesene Gesamtbetrag aus dem Vinted-Checkout.
       Der ist verbindlich; Versand und Käuferschutz stecken zusammen darin.
    2. `item.total_price` — Vinted liefert in den Angebotsdaten den Preis
       samt Käuferschutz. Dann wird nur noch der Versand geschätzt.
    3. Alles geschätzt, nach den Kostensätzen des Profils.

    Zweiter Rückgabewert: der Grund, warum keine Rechnung möglich war.
    """
    if item.price is None:
        return None, "kein Preis angegeben"
    kosten = profile.costs

    preis = cents(item.price)
    umrechnung: tuple[float, str, float] | None = None
    waehrung = (item.currency or profile.currency).upper()
    kurs = 1.0
    if waehrung != profile.currency.upper():
        gefunden = profile.rates.get(waehrung)
        if not gefunden or gefunden <= 0:
            return None, (
                f"Preis in {waehrung}, Profil rechnet in {profile.currency} — "
                f"Kurs fehlt in `[rates]`"
            )
        kurs = float(gefunden)
        umrechnung = (preis, waehrung, kurs)
        preis = cents(preis * kurs)

    if checkout_total is not None:
        gesamt = cents(checkout_total * kurs)
        if gesamt < preis:
            return None, "Checkout-Betrag liegt unter dem Artikelpreis"
        return (
            Bill(
                item_price=preis,
                shipping=0.0,
                protection=cents(gesamt - preis),
                refurb=cents(kosten.refurb),
                reserve=cents(kosten.reserve),
                resale_price=cents(profile.resale_price),
                currency=profile.currency,
                source=FROM_CHECKOUT,
                converted=umrechnung,
            ),
            None,
        )

    if item.total_price is not None and item.total_price > item.price:
        schutz = cents((item.total_price - item.price) * kurs)
        quelle = FROM_VINTED
    else:
        schutz = kosten.protection(preis)
        quelle = ESTIMATED
    return (
        Bill(
            item_price=preis,
            shipping=cents(kosten.shipping),
            protection=schutz,
            refurb=cents(kosten.refurb),
            reserve=cents(kosten.reserve),
            resale_price=cents(profile.resale_price),
            currency=profile.currency,
            source=quelle,
            converted=umrechnung,
        ),
        None,
    )


def evaluate(
    item: Item, profile: Profile, *, checkout_total: float | None = None
) -> Verdict | None:
    """Einen Fund gegen ein Profil rechnen.

    `None`, wenn der Artikel gar nicht zum Profil gehört — dann ist er kein
    abgelehnter Deal, sondern schlicht etwas anderes.

    `checkout_total` ist der im Vinted-Checkout abgelesene Gesamtbetrag. Ist
    er bekannt, wird nichts mehr geschätzt.
    """
    titel = f"{item.title} {item.brand or ''}".lower()

    if profile.match_all and any(wort not in titel for wort in profile.match_all):
        return None
    if profile.match_any and _contains(titel, profile.match_any) is None:
        return None

    grenzen = profile.thresholds
    notizen: list[str] = []

    ausgeschlossen = _contains(titel, profile.exclude)
    if ausgeschlossen:
        return Verdict(profile, RED, 0.0, 0.0, 0.0, (f"„{ausgeschlossen}“ im Titel",))

    rechnung, grund = _bill(item, profile, checkout_total=checkout_total)
    if rechnung is None:
        return Verdict(profile, RED, 0.0, 0.0, 0.0, (grund or "keine Rechnung möglich",))

    if profile.max_item_price and rechnung.item_price > cents(profile.max_item_price):
        return Verdict(
            profile,
            RED,
            0.0,
            0.0,
            0.0,
            (f"über der Meldegrenze von {profile.max_item_price:.0f} €",),
            rechnung,
        )

    groesse = normalize_size(item.size)
    if profile.sizes and groesse not in profile.sizes:
        return Verdict(
            profile, RED, 0.0, 0.0, 0.0, (f"Größe {groesse or 'unbekannt'}",)
        )

    zustand = (item.condition or "").strip()
    if profile.conditions and zustand.lower() not in [
        c.lower() for c in profile.conditions
    ]:
        return Verdict(
            profile, RED, 0.0, 0.0, 0.0, (f"Zustand „{zustand or 'unbekannt'}“",)
        )

    gesamt = rechnung.total
    gewinn = rechnung.profit
    rendite = rechnung.roi

    if profile.max_total_cost and gesamt > cents(profile.max_total_cost):
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (
                f"Gesamt-EK {gesamt:.2f} € über der Grenze von "
                f"{profile.max_total_cost:.0f} €",
            ),
            rechnung,
        )

    if gewinn < cents(grenzen.min_profit):
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (f"nur {gewinn:.2f} € Marge, nötig sind {grenzen.min_profit:.2f} €",),
            rechnung,
        )
    if rendite < grenzen.min_roi:
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (f"nur {rendite:.0f} % ROI, nötig sind {grenzen.min_roi:.0f} %",),
            rechnung,
        )

    # Ab hier ist der Fund kaufbar. Die Frage ist nur noch: grün oder gelb?
    note = GREEN

    if gewinn < cents(grenzen.green_profit):
        note = YELLOW
        notizen.append(
            f"Gewinn {gewinn:.2f} € nur knapp über {grenzen.min_profit:.0f} €"
        )
    if profile.top_sizes and groesse not in profile.top_sizes:
        note = YELLOW
        notizen.append(f"Größe {groesse}")
    if profile.top_conditions and zustand.lower() not in [
        c.lower() for c in profile.top_conditions
    ]:
        note = YELLOW
        notizen.append(f"Zustand „{zustand}“")

    if profile.colors:
        farbe = _contains(titel, profile.colors)
        if farbe is None:
            # Viele Titel nennen keine Farbe. Das ist kein Ausschluss, aber
            # ohne sie lässt sich der Wiederverkaufswert nicht einschätzen.
            note = YELLOW
            notizen.append("Farbe steht nicht im Titel")
        elif profile.top_colors and farbe not in profile.top_colors:
            note = YELLOW
            notizen.append(f"Farbe {farbe}")

    return Verdict(profile, note, gewinn, rendite, gesamt, tuple(notizen), rechnung)


def best_verdict(
    item: Item, profiles: list[Profile], *, checkout_total: float | None = None
) -> Verdict | None:
    """Das beste Urteil über mehrere Profile hinweg.

    Ein Zopfmuster-Pullover mit Viertelreißverschluss passt auf zwei Profile;
    gelten soll das mit der besseren Ampel, bei Gleichstand das mit der
    höheren Marge.
    """
    urteile = [
        v
        for v in (
            evaluate(item, p, checkout_total=checkout_total) for p in profiles
        )
        if v is not None
    ]
    if not urteile:
        return None
    rang = {GREEN: 0, YELLOW: 1, RED: 2}
    urteile.sort(key=lambda v: (rang.get(v.grade, 3), -v.profit))
    return urteile[0]


# Was die Angebotsdaten nicht hergeben und deshalb am Artikel geprüft werden
# muss, bevor gekauft wird. Steht bewusst im Alert und nicht in einer Doku, die
# man im entscheidenden Moment nicht offen hat.
CHECKLIST = (
    "**Checkout-Preis** ansehen — der zählt, nicht der Artikelpreis",
    "Pflegeetikett vorhanden, möglichst 100 % Baumwolle",
    "Fotos von Pony, Nacken-, Größen- und Pflegeetikett",
    "keine Löcher, Flecken, Verfärbungen, ausgeleierte Bündchen",
    "Reißverschluss läuft",
    "Maße passen plausibel zur Größenangabe",
    "Verkäufer bietet nicht zehn gleiche „neue“ Teile an",
    "eBay: 5 echte Verkäufe, **zweitniedrigsten** Preis nehmen",
)

# Fehlen Fotos oder Maße, ist die Nachricht immer dieselbe. Sie hier bereit zu
# haben spart im entscheidenden Moment eine Minute Tippen.
SELLER_QUESTION = (
    "Hi, könntest du bitte noch Fotos vom Nackenetikett, Pflegeetikett "
    "(Vorder- und Rückseite) sowie vom Logo schicken? Außerdem bräuchte ich "
    "Brustweite und Rückenlänge. Danke!"
)
