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

import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from .vinted.models import Item


@dataclass(frozen=True)
class Costs:
    """Was ein Artikel am Ende wirklich kostet.

    Die Voreinstellungen sind Schätzwerte für einen deutschen Standardversand.
    Käuferschutz und Versand ändert Vinted von Zeit zu Zeit und je nach Land —
    die Zahlen gehören einmal am echten Kaufabschluss gegengeprüft und dann in
    die `profiles.toml`.
    """

    shipping: float = 2.99
    protection_fixed: float = 0.70
    protection_percent: float = 5.0
    # Waschen, Dämpfen, Fusseln — pro Stück gerechnet.
    refurb: float = 0.0
    # Puffer für Preisnachlässe und kleine Überraschungen. Wird vom Gewinn
    # abgezogen, nicht auf die Kosten geschlagen: er ist Risiko, nicht Ausgabe.
    reserve: float = 3.0

    def total(self, price: float) -> float:
        """Einkaufspreis inklusive allem, was oben draufkommt."""
        return (
            price
            + self.shipping
            + self.protection_fixed
            + price * self.protection_percent / 100.0
            + self.refurb
        )


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


GREEN = "gruen"
YELLOW = "gelb"
RED = "rot"

_AMPEL = {GREEN: "🟢 GRÜN — kaufen", YELLOW: "🟡 GELB — erst nachfragen", RED: "🔴 ROT"}


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

    @property
    def accepted(self) -> bool:
        return self.grade in (GREEN, YELLOW)

    def headline(self) -> str:
        euro = f"{self.profit:.2f}".replace(".", ",")
        kosten = f"{self.total_cost:.2f}".replace(".", ",")
        return (
            f"**{_AMPEL[self.grade]}** · {self.profile.name}\n"
            f"≈ **+{euro} € Gewinn** bei {kosten} € Gesamt-EK · {self.roi:.0f} % ROI"
        )


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


def evaluate(item: Item, profile: Profile) -> Verdict | None:
    """Einen Fund gegen ein Profil rechnen.

    `None`, wenn der Artikel gar nicht zum Profil gehört — dann ist er kein
    abgelehnter Deal, sondern schlicht etwas anderes.
    """
    titel = f"{item.title} {item.brand or ''}".lower()

    if profile.match_all and any(wort not in titel for wort in profile.match_all):
        return None
    if profile.match_any and _contains(titel, profile.match_any) is None:
        return None

    kosten = profile.costs
    grenzen = profile.thresholds
    notizen: list[str] = []

    ausgeschlossen = _contains(titel, profile.exclude)
    if ausgeschlossen:
        return Verdict(profile, RED, 0.0, 0.0, 0.0, (f"„{ausgeschlossen}“ im Titel",))

    if item.price is None:
        return Verdict(profile, RED, 0.0, 0.0, 0.0, ("kein Preis angegeben",))
    if profile.max_item_price and item.price > profile.max_item_price:
        return Verdict(
            profile,
            RED,
            0.0,
            0.0,
            0.0,
            (f"über der Meldegrenze von {profile.max_item_price:.0f} €",),
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

    gesamt = kosten.total(item.price)
    gewinn = profile.resale_price - gesamt - kosten.reserve
    rendite = (gewinn / gesamt * 100.0) if gesamt > 0 else 0.0

    if profile.max_total_cost and gesamt > profile.max_total_cost:
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (
                f"geschätzter Gesamt-EK {gesamt:.2f} € über der Grenze von "
                f"{profile.max_total_cost:.0f} €",
            ),
        )

    if gewinn < grenzen.min_profit:
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (f"nur {gewinn:.0f} € Marge, nötig sind {grenzen.min_profit:.0f} €",),
        )
    if rendite < grenzen.min_roi:
        return Verdict(
            profile,
            RED,
            gewinn,
            rendite,
            gesamt,
            (f"nur {rendite:.0f} % ROI, nötig sind {grenzen.min_roi:.0f} %",),
        )

    # Ab hier ist der Fund kaufbar. Die Frage ist nur noch: grün oder gelb?
    note = GREEN

    if gewinn < grenzen.green_profit:
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

    return Verdict(profile, note, gewinn, rendite, gesamt, tuple(notizen))


def best_verdict(item: Item, profiles: list[Profile]) -> Verdict | None:
    """Das beste Urteil über mehrere Profile hinweg.

    Ein Zopfmuster-Pullover mit Viertelreißverschluss passt auf zwei Profile;
    gelten soll das mit der besseren Ampel, bei Gleichstand das mit der
    höheren Marge.
    """
    urteile = [v for v in (evaluate(item, p) for p in profiles) if v is not None]
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
