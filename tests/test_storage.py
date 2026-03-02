"""Tests for SQLite storage layer — no network calls."""
import time

import pytest

from arb.storage import init_db, insert_funding_snapshot, get_rolling_avg


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.sqlite")
    c = init_db(db_path)
    yield c
    c.close()


def test_rolling_avg_basic(conn):
    """Average of inserted rows within the window is correct."""
    now = int(time.time())
    insert_funding_snapshot(conn, now - 3600, "hyperliquid", "BTC", 0.0001)
    insert_funding_snapshot(conn, now - 1800, "hyperliquid", "BTC", 0.0003)
    insert_funding_snapshot(conn, now - 600, "hyperliquid", "BTC", 0.0002)

    since = now - 7200
    avg, window_hours = get_rolling_avg(conn, "hyperliquid", "BTC", since)

    assert avg is not None
    assert abs(avg - 0.0002) < 1e-10  # (0.0001 + 0.0003 + 0.0002) / 3
    # Window spans from (now-3600) to (now-600) = 3000 seconds = 0.833h
    assert window_hours == pytest.approx(3000 / 3600, rel=1e-3)


def test_rolling_avg_no_data(conn):
    """Returns (None, 0.0) when no rows exist for the pair."""
    since = int(time.time()) - 86400
    avg, window_hours = get_rolling_avg(conn, "hyperliquid", "ETH", since)
    assert avg is None
    assert window_hours == 0.0


def test_rolling_avg_excludes_old_rows(conn):
    """Rows older than since_ts are excluded from the average."""
    now = int(time.time())
    # Insert one old row (outside window) and one recent row
    insert_funding_snapshot(conn, now - 90000, "kucoin", "BTC", 0.9999)  # old
    insert_funding_snapshot(conn, now - 1000, "kucoin", "BTC", 0.0001)   # recent

    since = now - 86400  # 24h ago
    avg, window_hours = get_rolling_avg(conn, "kucoin", "BTC", since)

    # Only the recent row should be included
    assert avg is not None
    assert abs(avg - 0.0001) < 1e-10
    # Only 1 row → MAX(ts) == MIN(ts) → window_hours == 0.0
    assert window_hours == 0.0
