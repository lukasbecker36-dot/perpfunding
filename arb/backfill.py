"""Backfill historical funding rates on startup.

Fetches funding rate history from each exchange's API and inserts into
the SQLite DB so that rolling averages are meaningful immediately after
a cold start or Streamlit Cloud wake-up.
"""
from __future__ import annotations

import logging

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

_DEFAULT_LOOKBACK = 7 * 24 * 3600  # 7 days in seconds


def backfill_if_needed(db_path: str | None = None, lookback_hours: int = 7 * 24) -> None:
    """Backfill historical funding for all exchanges that support it.

    For each exchange in HISTORY_MAP:
      - Check the DB for the latest snapshot timestamp
      - If we have recent data, only fill the gap
      - Otherwise, do a full lookback backfill
      - Fetch a Loris snapshot first to get the active symbol list
    """
    if db_path is None:
        db_path = settings.DB_PATH

    # Get the symbol list from Loris so we only backfill tracked symbols
    loris_rows = loris.fetch_funding()
    symbols_by_exchange: dict[str, list[str]] = {}
    for row in loris_rows:
        ex = row["exchange"]
        symbols_by_exchange.setdefault(ex, []).append(row["symbol"])

    if not symbols_by_exchange:
        _log.warning("Backfill: no symbols from Loris, skipping")
        return

    conn = init_db(db_path)
    now = now_utc_epoch()
    since = now - lookback_hours * 3600

    try:
        for exchange, fetch_fn in HISTORY_MAP.items():
            symbols = symbols_by_exchange.get(exchange, [])
            if not symbols:
                _log.debug("Backfill: no symbols for %s, skipping", exchange)
                continue

            latest_ts = get_latest_snapshot_ts(conn, exchange)
            if latest_ts and latest_ts > since:
                fetch_since = latest_ts
                _log.info("Backfill %s: filling gap from %d (%.1fh ago)",
                          exchange, latest_ts, (now - latest_ts) / 3600)
            else:
                fetch_since = since
                _log.info("Backfill %s: full %dh lookback", exchange, lookback_hours)

            try:
                rows = fetch_fn(symbols, fetch_since)
                if rows:
                    n = insert_funding_snapshots_bulk(conn, exchange, rows)
                    _log.info("Backfill %s: inserted %d rows", exchange, n)
                else:
                    _log.info("Backfill %s: no historical data returned", exchange)
            except Exception:
                _log.warning("Backfill failed for %s", exchange, exc_info=True)
    finally:
        conn.close()
