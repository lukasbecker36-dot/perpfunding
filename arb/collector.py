"""Background funding-rate collector.

Polls Loris every POLL_INTERVAL seconds and inserts funding snapshots into
the SQLite database so that rolling 24h averages actually have data.
"""
from __future__ import annotations

import logging
import threading
import time

from . import loris
from .config import settings
from .storage import init_db, insert_funding_snapshot
from .timeutil import now_utc_epoch
from .venues import ALL_VENUES

_log = logging.getLogger(__name__)

POLL_INTERVAL = 300  # 5 minutes

_lock = threading.Lock()
_running = False
_thread: threading.Thread | None = None


def _collect_once(venues: list[str] | None = None) -> int:
    """Fetch funding from Loris and insert into DB.  Returns row count."""
    if venues is None:
        venues = ALL_VENUES
    ts = now_utc_epoch()
    conn = init_db(settings.DB_PATH)
    try:
        rows = loris.fetch_funding()
        n = 0
        for row in rows:
            if row["exchange"] in venues:
                insert_funding_snapshot(conn, ts, row["exchange"], row["symbol"], row["funding"])
                n += 1
        return n
    finally:
        conn.close()


def _loop(venues: list[str] | None, interval: int) -> None:
    global _running
    _log.info("Collector started (interval=%ds)", interval)
    while _running:
        try:
            n = _collect_once(venues)
            _log.info("Collected %d funding snapshots", n)
        except Exception:
            _log.exception("Collector iteration failed")
        # Sleep in small increments so we can stop quickly
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)
    _log.info("Collector stopped")


def start(venues: list[str] | None = None, interval: int = POLL_INTERVAL) -> None:
    """Start the background collector thread (idempotent)."""
    global _running, _thread
    with _lock:
        if _running:
            return
        _running = True
        _thread = threading.Thread(
            target=_loop, args=(venues, interval), daemon=True, name="funding-collector"
        )
        _thread.start()


def stop() -> None:
    """Signal the collector to stop."""
    global _running
    with _lock:
        _running = False


def is_running() -> bool:
    return _running
