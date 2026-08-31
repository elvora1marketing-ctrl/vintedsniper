"""Polling-Schleife: pro Watch ein Task, der neue Listings meldet."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

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

    # ------------------------------------------------------------- Lebenszyklus

    async def start_all(self) -> int:
        watches = await self.db.list_watches()
        started = 0
        for watch in watches:
            if watch.enabled:
                self.start(watch)
                started += 1
        if self._housekeeping is None:
            self._housekeeping = asyncio.create_task(self._housekeeping_loop())
        return started

    def start(self, watch: Watch) -> None:
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

        while True:
            watch = await self.db.get_watch(watch_id)
            if watch is None or not watch.enabled:
                log.info("Watch %s existiert nicht mehr oder ist pausiert.", watch_id)
                return

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

                new_ids = await self.db.filter_new(
                    watch_id,
                    [item.id for item in items],
                    scope=self.settings.dedupe_scope,
                    group_key=watch.group_key,
                )

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
                    await self.db.mark_checked(watch_id, error=None)
                else:
                    fresh = [item for item in items if item.id in new_ids]
                    fresh = [item for item in fresh if self._is_recent(item)]
                    # Älteste zuerst posten, damit die Discord-Timeline stimmt.
                    fresh.reverse()
                    if fresh:
                        log.info(
                            "Watch %s (%s): %d neue Artikel.",
                            watch_id,
                            watch.name,
                            len(fresh),
                        )
                        await self._safe_items(watch, fresh)
                    await self.db.mark_checked(watch_id, error=None, new_hits=len(fresh))

            await asyncio.sleep(self._with_jitter(delay))

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
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Housekeeping fehlgeschlagen.")
