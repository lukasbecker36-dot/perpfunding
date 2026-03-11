"""Backfill historical funding rates on startup.

Fetches funding rate history from each exchange's API and inserts into
the SQLite DB so that rolling averages are meaningful immediately after
a cold start or Streamlit Cloud wake-up.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import loris
from .config import settings
from .storage import (
    get_latest_snapshot_ts,
    init_db,
    insert_funding_snapshots_bulk,
)
from .timeutil import now_utc_epoch
from .venues import HISTORY_MAP

_log = logging.getLogger(__name__)

# Only backfill the top N symbols per exchange to keep startup fast.
_MAX_SYMBOLS_PER_EXCHANGE = 30


def backfill_if_needed(db_path: str | None = None, lookback_hours: int = 7 * 24) -> None:
    """Backfill historical funding for all exchanges in parallel."""
    if db_path is None:
        db_path = settings.DB_PATH

    # Get the symbol list from Loris — only backfill top symbols by |funding|.
    loris_rows = loris.fetch_funding()
    by_exchange: dict[str, list[dict]] = {}
    for row in loris_rows:
        by_exchange.setdefault(row["exchange"], []).append(row)
    symbols_by_exchange: dict[str, list[str]] = {}
    for ex, rows_list in by_exchange.items():
        rows_list.sort(key=lambda r: abs(r["funding"]), reverse=True)
        symbols_by_exchange[ex] = [r["symbol"] for r in rows_list[:_MAX_SYMBOLS_PER_EXCHANGE]]

    if not symbols_by_exchange:
        _log.warning("Backfill: no symbols from Loris, skipping")
        return

    conn = init_db(db_path)
    now = now_utc_epoch()
    since = now - lookback_hours * 3600

    # Build tasks: (exchange, fetch_fn, symbols, fetch_since)
    tasks: list[tuple[str, object, list[str], int]] = []
    for exchange, fetch_fn in HISTORY_MAP.items():
        symbols = symbols_by_exchange.get(exchange, [])
        if not symbols:
            continue
        latest_ts = get_latest_snapshot_ts(conn, exchange)
        if latest_ts and latest_ts > since:
            fetch_since = latest_ts
            _log.info("Backfill %s: filling gap from %.1fh ago (%d symbols)",
                      exchange, (now - latest_ts) / 3600, len(symbols))
        else:
            fetch_since = since
            _log.info("Backfill %s: full %dh lookback (%d symbols)",
                      exchange, lookback_hours, len(symbols))
        tasks.append((exchange, fetch_fn, symbols, fetch_since))

    # Fetch all exchanges in parallel
    def _do_backfill(exchange, fetch_fn, symbols, fetch_since):
        try:
            rows = fetch_fn(symbols, fetch_since)
            return exchange, rows
        except Exception:
            _log.warning("Backfill failed for %s", exchange, exc_info=True)
            return exchange, []

    try:
        with ThreadPoolExecutor(max_workers=len(tasks) or 1) as pool:
            futs = [pool.submit(_do_backfill, *t) for t in tasks]
            for fut in as_completed(futs):
                exchange, rows = fut.result()
                if rows:
                    n = insert_funding_snapshots_bulk(conn, exchange, rows)
                    _log.info("Backfill %s: inserted %d rows", exchange, n)
                else:
                    _log.info("Backfill %s: no historical data returned", exchange)
    finally:
        conn.close()
