"""SQLite persistence layer for funding snapshots and arb results."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

_log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS funding_snapshots (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    exchange TEXT    NOT NULL,
    symbol   TEXT    NOT NULL,
    funding  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_funding ON funding_snapshots (exchange, symbol, ts);
CREATE UNIQUE INDEX IF NOT EXISTS uq_funding ON funding_snapshots (ts, exchange, symbol);

CREATE TABLE IF NOT EXISTS arb_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    rank           INTEGER,
    exchange       TEXT,
    symbol         TEXT,
    funding_latest REAL,
    funding_avg_24h REAL,
    funding_avg_3d REAL,
    funding_avg_7d REAL,
    funding_window_hours REAL,
    perp_bid       REAL,
    perp_bid_size_usdt REAL,
    spot_price     REAL,
    basis_usd      REAL,
    basis_bps      REAL,
    est_gross_edge REAL,
    notes          TEXT
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database with WAL mode."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_DDL)
    # Migrate existing arb_snapshots table if missing new columns
    _migrate_arb_columns(conn)
    conn.commit()
    return conn


def _migrate_arb_columns(conn: sqlite3.Connection) -> None:
    """Add funding_avg_3d/7d columns if they don't exist (for existing DBs)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(arb_snapshots)").fetchall()}
    for col in ("funding_avg_3d", "funding_avg_7d"):
        if col not in existing:
            conn.execute(f"ALTER TABLE arb_snapshots ADD COLUMN {col} REAL")
            _log.info("Migrated arb_snapshots: added %s", col)


def insert_funding_snapshot(
    conn: sqlite3.Connection,
    ts: int,
    exchange: str,
    symbol: str,
    funding: float,
) -> None:
    conn.execute(
        "INSERT INTO funding_snapshots (ts, exchange, symbol, funding) VALUES (?, ?, ?, ?)",
        (ts, exchange, symbol, funding),
    )
    conn.commit()


def get_rolling_avg(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    since_ts: int,
) -> tuple[float | None, float]:
    """Return (avg_funding, window_hours) for the rolling window.

    window_hours = (MAX(ts) - MIN(ts)) / 3600 — reflects actual data coverage.
    Returns (None, 0.0) when no rows exist.
    """
    row = conn.execute(
        """
        SELECT AVG(funding),
               (MAX(ts) - MIN(ts)) / 3600.0,
               COUNT(*)
        FROM funding_snapshots
        WHERE exchange = ? AND symbol = ? AND ts >= ?
        """,
        (exchange, symbol, since_ts),
    ).fetchone()
    if row is None or row[2] == 0:
        return None, 0.0
    avg, window_hours, _ = row
    return avg, (window_hours or 0.0)


def insert_arb_snapshot(
    conn: sqlite3.Connection,
    ts: int,
    rows: list[dict],
) -> None:
    """Persist a batch of arb result rows."""
    for r in rows:
        conn.execute(
            """
            INSERT INTO arb_snapshots
              (ts, rank, exchange, symbol, funding_latest, funding_avg_24h,
               funding_avg_3d, funding_avg_7d,
               funding_window_hours, perp_bid, perp_bid_size_usdt, spot_price,
               basis_usd, basis_bps, est_gross_edge, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                r.get("rank"),
                r.get("exchange"),
                r.get("symbol"),
                r.get("funding_latest"),
                r.get("funding_avg_24h"),
                r.get("funding_avg_3d"),
                r.get("funding_avg_7d"),
                r.get("funding_window_hours"),
                r.get("perp_bid"),
                r.get("perp_bid_size_usdt"),
                r.get("spot_price"),
                r.get("basis_usd"),
                r.get("basis_bps"),
                r.get("est_gross_edge"),
                r.get("notes"),
            ),
        )
    conn.commit()


def get_latest_snapshot_ts(conn: sqlite3.Connection, exchange: str) -> int | None:
    """Return the most recent timestamp for an exchange, or None."""
    row = conn.execute(
        "SELECT MAX(ts) FROM funding_snapshots WHERE exchange = ?",
        (exchange,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def insert_funding_snapshots_bulk(
    conn: sqlite3.Connection,
    exchange: str,
    rows: list[tuple[int, str, float]],
) -> int:
    """Bulk insert historical funding rows with dedup.

    rows: list of (ts, symbol, funding) tuples.
    Returns number of rows inserted.
    """
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR IGNORE INTO funding_snapshots (ts, exchange, symbol, funding) VALUES (?, ?, ?, ?)",
        [(ts, exchange, symbol, funding) for ts, symbol, funding in rows],
    )
    conn.commit()
    return len(rows)


def prune_old_snapshots(conn: sqlite3.Connection, keep_days: int = 7) -> None:
    """Delete funding snapshots older than keep_days to bound DB size."""
    cutoff = int(__import__("time").time()) - keep_days * 86_400
    conn.execute("DELETE FROM funding_snapshots WHERE ts < ?", (cutoff,))
    conn.commit()
