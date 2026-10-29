"""The lyrics service: looks every indexed track up on LRCLIB in the background (SPEC §9, §16).

Four workers share one queue in three tiers: the selected track, then the open folder, then the rest of the
library folder by folder (read from the index in batches). A key is looked up once however many tracks share it.
Results go into the `lyrics` table through the DbWriter, so the state survives restarts: whatever has no fresh
entry is still pending at the next start.

Errors aren't cached (core.lyrics.lookup): a key that failed sits out for the pause delay before it is tried
again. After 10 failures in a row the circuit breaker pauses every lookup for 10 minutes; `retry_now` ends the
pause early. Offline (tests only since M12) makes no LRCLIB call at all.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from refrain.core.index.lyrics_index import (
    PendingTrack,
    pending_count,
    pending_tracks,
    record_lyrics,
    retry_before,
    track_for_entry,
)
from refrain.core.index.store import DbWriter, LyricsStore, connect_read
from refrain.core.lyrics import ERROR, NOT_LOOKED_UP, CircuitBreaker, LrclibClient, lookup

log = logging.getLogger(__name__)

PAUSE_SECONDS = 600  # the circuit breaker's pause, and an errored key's wait before its next try
BATCH = 200  # background keys read from the index at a time
PROGRESS_INTERVAL = 0.1  # seconds

SELECTED, OPEN_FOLDER, BACKGROUND = range(3)

# states
RUNNING = "running"
IDLE = "idle"  # nothing pending
UNREACHABLE = "unreachable"  # the circuit breaker's pause
OFFLINE = "offline"


@dataclass(frozen=True)
class LyricsProgress:
    state: str
    done: int  # lookups finished this session (errors included)
    errors: int  # of those, errors
    in_flight: int
    pending: int | None = None  # keys still to look up (errors included), once counted; None before the first count


class _Cache:
    """core.lyrics.LyricsCache for one worker: reads on its own connection, and writes through the writer with the
    folder totals (waiting for the commit, so a key is never pending again once it is reported done)."""

    def __init__(self, store: LyricsStore, writer: DbWriter) -> None:
        self._store = store
        self._writer = writer
        self.folders: list[int] = []
        self.bypass = False  # Retry now: look the key up again whatever the cache holds

    def get(self, key: str) -> dict | None:
        return None if self.bypass else self._store.get(key)

    def put(self, key: str, entry: dict) -> None:
        self.folders = self._writer.submit(lambda conn: record_lyrics(conn, key, entry)).result()


class LyricsService(QObject):
    entryReady = Signal(str, object)  # key, cache entry (or an error / not_looked_up entry)
    foldersChanged = Signal(object)  # list[int]: folders whose LRCLIB column changed
    progress = Signal(object)  # LyricsProgress
    unreachable = Signal(float)  # time.time() at which lookups resume
    resumed = Signal()

    def __init__(self, db_path: str | Path, writer: DbWriter, parent: QObject | None = None, *,
                 client: LrclibClient | None = None, offline: bool = False, workers: int | None = None,
                 pause_seconds: float = PAUSE_SECONDS, today: Callable[[], date] = date.today,
                 batch: int = BATCH) -> None:
        super().__init__(parent)
        self.db_path = Path(db_path)
        self._writer = writer
        self._client = client or LrclibClient()
        self._pause_seconds = pause_seconds
        self._today = today
        self._batch = batch
        self._cond = threading.Condition()
        self._tiers: list[deque[PendingTrack]] = [deque(), deque(), deque()]
        self._requests: deque[tuple[str, int]] = deque()  # ("track" | "folder", entry id), resolved by a worker
        self._in_flight: set[str] = set()
        self._handled: set[str] = set()  # looked up this session (the index has their entry)
        self._errored: dict[str, float] = {}  # key -> monotonic time of the next try
        self._last_error: dict[str, str] = {}  # key -> the message of its last failed lookup
        self._forced: set[str] = set()  # keys to look up again whatever the cache holds
        self._breaker = CircuitBreaker(self._client.options.max_consecutive_failures)
        self._paused_until: float | None = None  # monotonic
        self._resume_at = 0.0  # time.time() at which the breaker's pause ends
        self._offline = offline
        self._started = False
        self._refilling = False
        self._exhausted = False  # the background has nothing left until wake()
        self._stopping = False
        self._done = self._errors = 0
        self._pending: int | None = None  # counted at each background refill, then decremented per result
        self._last_progress = 0.0
        self._last_state = ""
        count = workers or self._client.options.lookup_threads
        self._threads = [threading.Thread(target=self._run, name=f"lyrics-{n}", daemon=True) for n in range(count)]
        for thread in self._threads:
            thread.start()

    # -- requests (any thread; they return at once) --

    def start(self) -> None:
        """Begin the background pass (once the tags are indexed)."""
        with self._cond:
            self._started = True
            self._exhausted = False
            self._cond.notify_all()

    def wake(self) -> None:
        """New tracks or changed tags may have made keys pending."""
        with self._cond:
            self._exhausted = False
            self._cond.notify_all()

    def prioritize_track(self, entry_id: int) -> None:
        """The selected track goes first."""
        self._request("track", entry_id)

    def prioritize_folder(self, folder_id: int) -> None:
        """The open folder's tracks go next (replacing the previously open folder's)."""
        self._request("folder", folder_id)

    def retry(self, entry_id: int) -> None:
        """Look one track up again now, whatever its cache entry says (the Lyrics tab's Retry now). It also ends
        the circuit breaker's pause: the user asked for a try."""
        self._end_pause()
        self._request("retry", entry_id)

    def last_error(self, key: str) -> str | None:
        """Why the key's last lookup this session failed, until one succeeds."""
        with self._cond:
            return self._last_error.get(key)

    @property
    def resume_at(self) -> float:
        """time.time() at which the circuit breaker's pause ends (meaningful in the unreachable state)."""
        with self._cond:
            return self._resume_at

    def status_for(self, key: str) -> str | None:
        """What the LRCLIB column shows for a key without an entry: "offline", "error" (its last lookup failed),
        or None (pending)."""
        with self._cond:
            if self._offline:
                return "offline"
            return "error" if key in self._last_error else None

    def error_keys(self) -> frozenset[str]:
        """The keys whose last lookup this session failed (library search's `lrclib:error`)."""
        with self._cond:
            return frozenset(self._last_error)

    def set_offline(self, offline: bool) -> None:
        with self._cond:
            self._offline = offline
            self._cond.notify_all()
        self._emit_progress(force=True)

    def retry_now(self) -> None:
        """End the circuit breaker's pause and give the keys that failed another try."""
        with self._cond:
            self._errored.clear()
        self._end_pause()

    def _end_pause(self) -> None:
        with self._cond:
            was_paused = self._paused_until is not None
            self._paused_until = None
            self._breaker.reset()
            self._exhausted = False
            self._cond.notify_all()
        if was_paused:
            self.resumed.emit()
        self._emit_progress(force=True)

    @property
    def state(self) -> str:
        with self._cond:
            return self._state_locked()

    def stop(self, timeout: float | None = 5) -> None:
        """Stop taking work; a lookup in progress finishes (or is abandoned after the timeout: daemon threads)."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))

    # -- the workers --

    def _request(self, kind: str, entry_id: int) -> None:
        with self._cond:
            if self._stopping:
                return
            self._requests.append((kind, entry_id))
            self._cond.notify()

    def _run(self) -> None:
        try:
            conn = connect_read(self.db_path)
        except Exception:  # noqa: BLE001 - without its connection this worker can't serve
            log.exception("lyrics worker could not open the index")
            return
        cache = _Cache(LyricsStore(conn, self._writer), self._writer)
        try:
            while True:
                item = self._next(conn)
                if item is None:
                    return
                cache.folders = []
                with self._cond:
                    cache.bypass = item.key in self._forced
                    self._forced.discard(item.key)
                try:
                    entry = lookup(item.track, cache, self._client, offline=False, today=self._today())
                except Exception as e:  # noqa: BLE001 - e.g. the writer stopped during shutdown
                    entry = {"status": ERROR, "error": f"{type(e).__name__}: {e}"}
                self._finish(item, entry, cache.folders)
        finally:
            conn.close()

    def _next(self, conn) -> PendingTrack | None:
        """The next track to look up, resolving priority requests and refilling the background on the way.
        None when stopping."""
        while True:
            request = refill = None
            with self._cond:
                while True:
                    if self._stopping:
                        return None
                    if self._requests:
                        request = self._requests.popleft()
                        break
                    timeout = self._check_pause_locked()
                    if self._can_work_locked():
                        item = self._take_locked()
                        if item is not None:
                            return item
                        if self._started and not self._refilling and not self._exhausted:
                            self._refilling = refill = True
                            break
                        timeout = self._next_retry_locked(timeout)
                    self._cond.wait(timeout)
            if request is not None:
                self._resolve(conn, *request)
            elif refill:
                self._refill(conn)

    def _can_work_locked(self) -> bool:
        return not (self._offline or self._paused_until is not None)

    def _check_pause_locked(self) -> float | None:
        """End the breaker's pause when its time is up; otherwise the seconds left (None: not paused)."""
        if self._paused_until is None:
            return None
        left = self._paused_until - time.monotonic()
        if left > 0:
            return left
        self._paused_until = None
        self._breaker.reset()
        self._exhausted = False
        self.resumed.emit()
        return None

    def _next_retry_locked(self, timeout: float | None) -> float | None:
        """With the background exhausted, wake up when the first errored key may be tried again."""
        if not self._errored:
            return timeout
        left = max(0.0, min(self._errored.values()) - time.monotonic())
        if left == 0:
            self._exhausted = False
        return left if timeout is None else min(timeout, left)

    def _take_locked(self) -> PendingTrack | None:
        now = time.monotonic()
        for tier, queue in enumerate(self._tiers):
            while queue:
                item = queue.popleft()
                if item.key in self._in_flight or item.key in self._handled:
                    continue
                if tier != SELECTED and self._errored.get(item.key, 0) > now:
                    continue  # failed recently; a selected track is tried anyway
                self._in_flight.add(item.key)
                return item
        return None

    def _resolve(self, conn, kind: str, entry_id: int) -> None:
        before = retry_before(self._today(), self._client.options)
        try:
            if kind == "retry":
                items = [item for item in [track_for_entry(conn, entry_id)] if item is not None]
            elif kind == "track":
                items = pending_tracks(conn, before, entry_id=entry_id)
            else:
                items = pending_tracks(conn, before, folder_id=entry_id)
        except Exception:  # noqa: BLE001 - a priority hint is never worth failing for
            log.exception("lyrics: could not read the %s %s", kind, entry_id)
            return
        with self._cond:
            if kind == "retry":
                for item in items:
                    self._handled.discard(item.key)
                    self._errored.pop(item.key, None)
                    self._forced.add(item.key)
                self._tiers[SELECTED].extendleft(items)
            elif kind == "track":
                self._tiers[SELECTED].extendleft(items)
            else:
                self._tiers[OPEN_FOLDER] = deque(items)
            if items:
                self._cond.notify_all()

    def _refill(self, conn) -> None:
        with self._cond:
            now = time.monotonic()
            self._errored = {k: t for k, t in self._errored.items() if t > now}
            skip = self._in_flight | self._handled | set(self._errored)
            skip |= {item.key for queue in self._tiers for item in queue}
        before = retry_before(self._today(), self._client.options)
        try:
            items = pending_tracks(conn, before, skip=skip, limit=self._batch)
            total = pending_count(conn, before)
        except Exception:  # noqa: BLE001 - tried again on the next wake()
            log.exception("lyrics: could not read the pending tracks")
            items, total = [], None
        with self._cond:
            if total is not None:
                self._pending = total
            self._refilling = False
            self._tiers[BACKGROUND].extend(items)
            self._exhausted = not items
            self._cond.notify_all()
        self._emit_progress(force=True)

    def _finish(self, item: PendingTrack, entry: dict, folders: list[int]) -> None:
        tripped = False
        with self._cond:
            self._in_flight.discard(item.key)
            self._done += 1
            status = entry.get("status")
            if status == ERROR:
                self._errors += 1
                self._errored[item.key] = time.monotonic() + self._pause_seconds
                self._last_error[item.key] = entry.get("error") or "unknown error"
                log.info("lyrics lookup failed for %r: %s", item.key, entry.get("error"))
            elif status != NOT_LOOKED_UP:
                if self._pending and item.key not in self._handled:
                    self._pending -= 1
                self._handled.add(item.key)
                self._errored.pop(item.key, None)
                self._last_error.pop(item.key, None)
            self._breaker.record(entry)
            if self._breaker.tripped and self._paused_until is None and not self._stopping:
                self._paused_until = time.monotonic() + self._pause_seconds
                self._resume_at = time.time() + self._pause_seconds
                tripped = True
        if self._stopping:
            return
        self.entryReady.emit(item.key, entry)
        if folders:
            self.foldersChanged.emit(folders)
        if tripped:
            log.warning("LRCLIB unreachable: %d failures in a row, pausing lookups", self._breaker.consecutive)
            self.unreachable.emit(self._resume_at)
        self._emit_progress(force=tripped)

    # -- progress --

    def _state_locked(self) -> str:
        if self._offline:
            return OFFLINE
        if self._paused_until is not None:
            return UNREACHABLE
        if self._in_flight or any(self._tiers) or self._requests or (self._started and not self._exhausted):
            return RUNNING
        return IDLE

    def _emit_progress(self, force: bool = False) -> None:
        with self._cond:
            progress = LyricsProgress(self._state_locked(), self._done, self._errors, len(self._in_flight),
                                      self._pending)
        now = time.monotonic()
        if not force and progress.state == self._last_state and now - self._last_progress < PROGRESS_INTERVAL:
            return
        self._last_progress, self._last_state = now, progress.state
        self.progress.emit(progress)
