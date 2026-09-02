"""Polling-Schleife: pro Watch ein Task, der neue Listings meldet."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import replace
from typing import Awaitable, Callable

from . import deals, pricing
from .config import Settings
from .db import Database, Watch
from .vinted.client import VintedClient
from .vinted.models import Item
from .vinted.session import Blocked, VintedError

log = logging.getLogger(__name__)

ItemsCallback = Callable[[Watch, list[Item]], Awaitable[None]]
NoticeCallback = Callable[[Watch, str], Awaitable[None]]

# So oft darf eine Watch am Stück scheitern, bevor der Channel eine Meldung sieht.
ERRORS_BEFORE_NOTICE = 5


class Monitor:
    """Verwaltet einen Hintergrund-Task pro aktiver Watch."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        client: VintedClient,
        *,
        on_items: ItemsCallback,
        on_trouble: NoticeCallback,
        on_recovered: NoticeCallback,
    ) -> None:
        self.settings = settings
        self.db = db
        self.client = client
        self._on_items = on_items
        self._on_trouble = on_trouble
        self._on_recovered = on_recovered

        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._housekeeping: asyncio.Task[None] | None = None
        # Kaufprofile. Leer = jeder neue Treffer wird gemeldet; gesetzt = nur
        # noch das, was nach allen Kosten genug Marge lässt.
        self.profiles: list[deals.Profile] = []
        # Zeitfenster fürs Abfragen. None = rund um die Uhr.
        self.window = getattr(settings, "active_hours", None)

    @property
    def paused(self) -> bool:
        """Gerade außerhalb des Zeitfensters?"""
        return self.window is not None and not self.window.is_open()

    # ------------------------------------------------------------- Lebenszyklus

    async def start_all(self) -> int:
        if not self.settings.polling_enabled:
            # POLLING=off: keine einzige Anfrage an Vinted. Alles andere —
            # Panel, Bewertung, Entdopplung, Discord — läuft weiter.
            log.warning(
                "POLLING=off: der Sniper fragt Vinted nicht von sich aus ab. "
                "Treffer kommen aus Vinteds eigenen Benachrichtigungen und "
                "werden hier nur bewertet (`/pruefen` oder im Panel)."
            )
            return 0

        watches = await self.db.list_watches()
        started = 0
        for watch in watches:
            if watch.enabled:
                self.start(watch)
                started += 1
        # Im Log nachlesbar, was die Entdopplung gerade abdeckt. Wer trotzdem
        # Doppel-Alerts sieht, sieht hier als Erstes, ob der Modus stimmt.
        gruppen = {watch.group_key for watch in watches if watch.enabled}
        log.info(
            "Entdopplung: Modus %s — %d Suche(n) in %d Gruppe(n)%s.",
            self.settings.dedupe_scope,
            started,
            len(gruppen),
            (
                ", " + str(sum(1 for w in watches if w.enabled and not w.group_key))
                + " ohne Gruppenkennung"
                if any(w.enabled and not w.group_key for w in watches)
                else ""
            ),
        )
        if self._housekeeping is None:
            self._housekeeping = asyncio.create_task(self._housekeeping_loop())
        return started

    def start(self, watch: Watch) -> None:
        if not self.settings.polling_enabled:
            return
        self.stop(watch.id)
        self._tasks[watch.id] = asyncio.create_task(
            self._run(watch.id), name=f"watch-{watch.id}"
        )

    def stop(self, watch_id: int) -> None:
        task = self._tasks.pop(watch_id, None)
        if task is not None and not task.done():
            task.cancel()

    def is_running(self, watch_id: int) -> bool:
        task = self._tasks.get(watch_id)
        return task is not None and not task.done()

    async def shutdown(self) -> None:
        for watch_id in list(self._tasks):
            self.stop(watch_id)
        if self._housekeeping is not None:
            self._housekeeping.cancel()
            self._housekeeping = None
        # Den Tasks kurz Zeit geben, ihre Cancellation zu verarbeiten.
        await asyncio.sleep(0)
        await self.client.close()

    # ------------------------------------------------------------------- Schleife

    async def _run(self, watch_id: int) -> None:
        # Beim Start leicht versetzt loslegen, damit nicht alle Watches
        # gleichzeitig auf dieselbe Domain feuern.
        await asyncio.sleep(random.uniform(0, 3))

        # Nach einem Neustart ist eine bekannte Watch bereits geprimed — sonst
        # würde jeder Neustart einen kompletten Durchlauf stumm schlucken.
        primed = await self.db.has_seen_any(watch_id)
        consecutive_errors = 0
        notified_trouble = False
        pause_gemeldet = False

        while True:
            watch = await self.db.get_watch(watch_id)
            if watch is None or not watch.enabled:
                log.info("Watch %s existiert nicht mehr oder ist pausiert.", watch_id)
                return

            # Außerhalb des Zeitfensters: keine Anfrage, kein Volumen. Höchstens
            # eine Stunde am Stück schlafen, damit eine Änderung am Fenster
            # nicht erst am nächsten Morgen greift.
            if self.paused:
                warte = self.window.seconds_until_open() if self.window else 0.0
                if not pause_gemeldet:
                    pause_gemeldet = True
                    log.info(
                        "Watch %s (%s): Pause, %s.",
                        watch_id,
                        watch.name,
                        self.window.describe_now() if self.window else "",
                    )
                await asyncio.sleep(min(warte, 3600.0) + random.uniform(0, 5))
                continue
            pause_gemeldet = False

            delay = watch.interval
            try:
                items = await self.client.search(watch.query)
            except asyncio.CancelledError:
                raise
            except (Blocked, VintedError) as exc:
                consecutive_errors += 1
                message = str(exc)
                log.warning("Watch %s (%s): %s", watch_id, watch.name, message)
                await self.db.mark_checked(watch_id, error=message)
                if consecutive_errors >= ERRORS_BEFORE_NOTICE and not notified_trouble:
                    notified_trouble = True
                    await self._safe_notice(self._on_trouble, watch, message)
                # Bei Blockaden bremsen wir deutlich stärker als bei Netzfehlern.
                factor = min(2 ** min(consecutive_errors, 4), 16)
                delay = min(watch.interval * factor, 900)
            except Exception:
                consecutive_errors += 1
                log.exception("Watch %s: unerwarteter Fehler", watch_id)
                await self.db.mark_checked(watch_id, error="Interner Fehler (siehe Log).")
                delay = min(watch.interval * 4, 600)
            else:
                if consecutive_errors and notified_trouble:
                    await self._safe_notice(
                        self._on_recovered, watch, "Verbindung steht wieder."
                    )
                consecutive_errors = 0
                notified_trouble = False

                # Vor allem anderen: die Preise aller gesehenen Artikel in die
                # Vergleichsbasis. Auch die schon bekannten — die Basis soll
                # den Markt abbilden, nicht nur die Zugänge.
                await self.db.record_prices(
                    watch.group_key,
                    [
                        (item.id, item.price, item.currency)
                        for item in items
                        if item.price is not None
                    ],
                )

                new_ids = await self.db.filter_new(
                    watch_id,
                    [item.id for item in items],
                    scope=self.settings.dedupe_scope,
                    group_key=watch.group_key,
                    prints={item.id: item.fingerprint() for item in items},
                )
                # Wie viel diese Suche beigetragen hat, das nicht ohnehin eine
                # Schwestersuche gefunden hätte. Bei Länderkopien ist das die
                # Zahl, an der man entscheidet, ob sie sich lohnen.
                dupes = self.db.last_duplicates

                if not primed:
                    # Erster Durchlauf: nur den Ist-Zustand einlesen. Sonst
                    # würde jede neue Watch sofort 20 Altbestand-Alerts feuern.
                    primed = True
                    log.info(
                        "Watch %s (%s): %d Artikel als Ausgangsbestand erfasst.",
                        watch_id,
                        watch.name,
                        len(new_ids),
                    )
                    await self.db.mark_checked(watch_id, error=None, dupes=dupes)
                else:
                    fresh = [item for item in items if item.id in new_ids]
                    fresh = [item for item in fresh if self._is_recent(item)]
                    fresh = [item for item in fresh if self._is_wanted(item)]
                    fresh = await self._only_deals(watch, fresh)
                    fresh = await self._annotate(watch, fresh)
                    # Älteste zuerst posten, damit die Discord-Timeline stimmt.
                    fresh.reverse()
                    # Profile schlagen die Reihenfolge: ein A-Deal wartet nicht,
                    # bis drei B-Deals gepostet sind.
                    fresh = self._graded(fresh)
                    if fresh:
                        log.info(
                            "Watch %s (%s): %d neue Artikel.",
                            watch_id,
                            watch.name,
                            len(fresh),
                        )
                        await self._safe_items(watch, fresh)
                    await self.db.mark_checked(
                        watch_id, error=None, new_hits=len(fresh), dupes=dupes
                    )

            await asyncio.sleep(self._with_jitter(delay))

    def _graded(self, items: list[Item]) -> list[Item]:
        """Funde bewerten, Aussortiertes verwerfen, A vor B stellen.

        Ohne Profile bleibt alles wie es ist — der Sniper meldet dann jeden
        neuen Treffer, wie vor dieser Erweiterung.
        """
        if not self.profiles or not items:
            return items

        bewertet: list[tuple[int, float, Item]] = []
        verworfen = 0
        for item in items:
            urteil = deals.best_verdict(item, self.profiles)
            if urteil is None:
                # Gehört zu keinem Profil — nichts, wofür wir hier sind.
                verworfen += 1
                continue
            if not urteil.accepted:
                log.debug(
                    "%s abgelehnt (%s): %s",
                    item.id,
                    urteil.profile.name,
                    "; ".join(urteil.notes) or "unter der Schwelle",
                )
                verworfen += 1
                continue
            bewertet.append(
                (0 if urteil.grade == deals.GREEN else 1, -urteil.profit,
                 replace(item, verdict=urteil))
            )

        if verworfen:
            log.info(
                "%d von %d Funden erfüllen die Kaufprofile nicht.",
                verworfen,
                len(items),
            )
        bewertet.sort(key=lambda eintrag: (eintrag[0], eintrag[1]))
        return [item for _, _, item in bewertet]

    def _is_wanted(self, item: Item) -> bool:
        """Offensichtlichen Ausschuss aussortieren, bevor er einen Alert kostet.

        Kaputte Ware und Fälschungen stehen fast immer im Titel. Ein Artikel
        ohne Foto ist beim Weiterverkauf wertlos, und Ein-Euro-Posten sind
        selten das, wonach jemand sucht.
        """
        if self.settings.min_price > 0 and item.price is not None:
            if item.price < self.settings.min_price:
                return False
        if self.settings.require_photo and not item.photo_url:
            return False
        if self.settings.exclude_words:
            titel = item.title.lower()
            if any(wort in titel for wort in self.settings.exclude_words):
                return False
        return True

    async def _only_deals(self, watch: Watch, items: list[Item]) -> list[Item]:
        """Nur melden, was deutlich unter dem Marktpreis liegt.

        Ohne Schwelle (`MIN_DISCOUNT=0`) bleibt alles drin. Ohne belastbare
        Vergleichsbasis ebenfalls — sonst wäre eine frisch angelegte Suche
        stundenlang stumm, also genau dann, wenn man sie beobachtet.
        """
        if not items or self.settings.min_discount <= 0:
            return items

        gefiltert: list[Item] = []
        for item in items:
            stats = await self._price_stats(watch, item)
            rabatt = pricing.discount(item.price, stats)
            if pricing.is_deal(rabatt, self.settings.min_discount):
                gefiltert.append(item)
            else:
                log.debug(
                    "Watch %s: %s übersprungen (%.0f %% unter Median, nötig %.0f %%).",
                    watch.id,
                    item.id,
                    rabatt or 0.0,
                    self.settings.min_discount,
                )
        if len(gefiltert) < len(items):
            log.info(
                "Watch %s (%s): %d von %d Treffern waren keine Schnäppchen.",
                watch.id,
                watch.name,
                len(items) - len(gefiltert),
                len(items),
            )
        return gefiltert

    async def _price_stats(self, watch: Watch, item: Item):
        preise = await self.db.recent_prices(
            watch.group_key, item.currency, days=self.settings.price_window_days
        )
        return pricing.stats_from(preise)

    async def _annotate(self, watch: Watch, items: list[Item]) -> list[Item]:
        """Jedem Fund seine Preiseinordnung mitgeben („38 % unter Median")."""
        annotiert: list[Item] = []
        for item in items:
            stats = await self._price_stats(watch, item)
            notiz = pricing.label(pricing.discount(item.price, stats), stats)
            annotiert.append(replace(item, price_note=notiz) if notiz else item)
        return annotiert

    def _is_recent(self, item: Item) -> bool:
        """Uralte Listings rausfiltern, die Vinted gelegentlich neu einsortiert."""
        if self.settings.max_item_age <= 0:
            return True
        age = item.age_seconds
        # Ohne Zeitstempel im Zweifel melden — lieber ein Alert zu viel.
        return age is None or age <= self.settings.max_item_age

    def _with_jitter(self, delay: float) -> float:
        if self.settings.jitter <= 0:
            return delay
        spread = delay * self.settings.jitter
        return max(1.0, delay + random.uniform(-spread, spread))

    async def _safe_items(self, watch: Watch, items: list[Item]) -> None:
        try:
            await self._on_items(watch, items)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Alerts für Watch %s konnten nicht zugestellt werden.", watch.id)

    async def _safe_notice(self, callback: NoticeCallback, watch: Watch, text: str) -> None:
        try:
            await callback(watch, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Statusmeldung für Watch %s fehlgeschlagen.", watch.id)

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(6 * 3600)
            try:
                removed = await self.db.prune_seen()
                if removed:
                    log.info("Housekeeping: %d alte Item-Einträge entfernt.", removed)
                # Preise leben länger als die Merkzettel: sie sind die
                # Vergleichsbasis und werden mit der Zeit besser.
                veraltet = await self.db.prune_prices(
                    older_than_days=max(60, self.settings.price_window_days * 2)
                )
                if veraltet:
                    log.info("Housekeeping: %d alte Preisdaten entfernt.", veraltet)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Housekeeping fehlgeschlagen.")
