"""Kaufprofile aus `profiles.toml` einlesen.

Getrennt von `deals.py`, damit die Rechenlogik ohne Datei und ohne TOML prüfbar
bleibt. Hier passiert nur das Übersetzen von Datei nach `Profile` — mit
Fehlermeldungen, die sagen, welcher Block gemeint ist.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from .deals import Costs, Profile, Thresholds

log = logging.getLogger(__name__)


class InvalidProfileFile(ValueError):
    """Die Profildatei ist unbrauchbar — mit einer Meldung, die weiterhilft."""


def _strings(raw: Any, position: str, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise InvalidProfileFile(f"{position}: `{key}` muss eine Liste von Texten sein.")
    # Kleingeschrieben, weil auch der Titel kleingeschrieben verglichen wird.
    return tuple(x.strip().lower() for x in raw if x.strip())


def _labels(raw: Any, position: str, key: str) -> tuple[str, ...]:
    """Wie `_strings`, aber die Schreibweise bleibt — für Zustände und Größen,
    die im Alert wieder auftauchen."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise InvalidProfileFile(f"{position}: `{key}` muss eine Liste von Texten sein.")
    return tuple(x.strip() for x in raw if x.strip())


def _number(raw: Any, position: str, key: str, default: float) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise InvalidProfileFile(f"{position}: `{key}` muss eine Zahl sein.")
    return float(raw)


def _costs(raw: dict[str, Any], position: str, fallback: Costs) -> Costs:
    return Costs(
        shipping=_number(raw.get("shipping"), position, "shipping", fallback.shipping),
        protection_fixed=_number(
            raw.get("protection_fixed"), position, "protection_fixed",
            fallback.protection_fixed,
        ),
        protection_percent=_number(
            raw.get("protection_percent"), position, "protection_percent",
            fallback.protection_percent,
        ),
        refurb=_number(raw.get("refurb"), position, "refurb", fallback.refurb),
        reserve=_number(raw.get("reserve"), position, "reserve", fallback.reserve),
    )


def _thresholds(raw: dict[str, Any], position: str, fallback: Thresholds) -> Thresholds:
    return Thresholds(
        min_profit=_number(
            raw.get("min_profit"), position, "min_profit", fallback.min_profit
        ),
        min_roi=_number(raw.get("min_roi"), position, "min_roi", fallback.min_roi),
        green_profit=_number(
            raw.get("green_profit"), position, "green_profit", fallback.green_profit
        ),
    )


def load_profiles(path: Path, *, allow_missing: bool = True) -> list[Profile]:
    """Profile einlesen.

    Eine fehlende Datei ist kein Fehler: ohne Profile meldet der Sniper wie
    bisher jeden Treffer. Eine *kaputte* Datei dagegen schon — sonst liefe der
    Sniper stillschweigend ohne die Filter weiter, auf die man sich verlässt.
    """
    if not path.exists():
        if allow_missing:
            return []
        raise InvalidProfileFile(f"{path} gibt es nicht.")
    if path.is_dir():
        raise InvalidProfileFile(
            f"{path} ist ein Verzeichnis, keine Datei. Das passiert, wenn Docker "
            "gestartet wurde, bevor die Datei existierte. Behebung:\n"
            f"  docker compose down && rm -rf {path} && "
            "cp profiles.example.toml profiles.toml && docker compose up -d"
        )

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidProfileFile(f"{path} ist kein gültiges TOML: {exc}") from exc

    basis_costs = _costs(data.get("costs") or {}, "[costs]", Costs())
    basis_grenzen = _thresholds(data.get("thresholds") or {}, "[thresholds]", Thresholds())
    waehrung = _currency(data.get("currency"), "currency")
    kurse = _rates(data.get("rates"), waehrung)

    roh = data.get("profile")
    if roh is None:
        return []
    if not isinstance(roh, list):
        raise InvalidProfileFile(
            f"`profile` muss in {path} als [[profile]] geschrieben werden "
            "(doppelte Klammern), sonst versteht TOML es nicht als Liste."
        )

    profile: list[Profile] = []
    namen: set[str] = set()

    for index, eintrag in enumerate(roh, start=1):
        position = f"[[profile]] Nr. {index}"
        if not isinstance(eintrag, dict):
            raise InvalidProfileFile(f"{position} ist kein Block mit Schlüsseln.")

        name = eintrag.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidProfileFile(f"{position}: `name` fehlt.")
        name = name.strip()[:60]
        if name in namen:
            raise InvalidProfileFile(f"{position}: den Namen „{name}“ gibt es schon.")
        namen.add(name)

        resale = _number(eintrag.get("resale_price"), position, "resale_price", 0.0)
        if resale <= 0:
            raise InvalidProfileFile(
                f"{position}: `resale_price` fehlt. Ohne einen erwarteten "
                "Verkaufspreis lässt sich keine Marge rechnen."
            )

        profile.append(
            Profile(
                name=name,
                match_any=_strings(eintrag.get("match_any"), position, "match_any"),
                match_all=_strings(eintrag.get("match_all"), position, "match_all"),
                exclude=_strings(eintrag.get("exclude"), position, "exclude"),
                resale_price=resale,
                max_item_price=_number(
                    eintrag.get("max_item_price"), position, "max_item_price", 0.0
                ),
                max_total_cost=_number(
                    eintrag.get("max_total_cost"), position, "max_total_cost", 0.0
                ),
                sizes=_labels(eintrag.get("sizes"), position, "sizes"),
                top_sizes=_labels(eintrag.get("top_sizes"), position, "top_sizes"),
                colors=_strings(eintrag.get("colors"), position, "colors"),
                top_colors=_strings(eintrag.get("top_colors"), position, "top_colors"),
                conditions=_labels(eintrag.get("conditions"), position, "conditions"),
                top_conditions=_labels(
                    eintrag.get("top_conditions"), position, "top_conditions"
                ),
                costs=_costs(eintrag.get("costs") or {}, position, basis_costs),
                thresholds=_thresholds(
                    eintrag.get("thresholds") or {}, position, basis_grenzen
                ),
                currency=waehrung,
                rates=kurse,
            )
        )

    return profile


def _currency(raw: Any, key: str) -> str:
    if raw is None:
        return "EUR"
    if not isinstance(raw, str) or len(raw.strip()) != 3:
        raise InvalidProfileFile(f"`{key}` muss ein Währungskürzel wie EUR sein.")
    return raw.strip().upper()


def _rates(raw: Any, currency: str) -> dict[str, float]:
    """`[rates]`: Fremdwährung → Profilwährung.

    Ein Kurs von 0 oder darunter ist kein Kurs. Und die Profilwährung selbst
    braucht keinen — 1,0 wird immer angenommen.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvalidProfileFile("`[rates]` muss ein Block mit `GBP = 1.17` usw. sein.")
    kurse: dict[str, float] = {}
    for code, wert in raw.items():
        if isinstance(wert, bool) or not isinstance(wert, (int, float)) or wert <= 0:
            raise InvalidProfileFile(f"[rates]: `{code}` muss eine Zahl über 0 sein.")
        kurse[str(code).strip().upper()] = float(wert)
    kurse.pop(currency, None)
    return kurse
