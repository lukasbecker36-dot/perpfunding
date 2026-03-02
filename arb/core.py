"""Core orchestration: fetch, enrich, compute arb metrics, return DataFrame."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from . import loris, dexscreener
from .config import settings
from .storage import (
    init_db,
    insert_funding_snapshot,
    get_rolling_avg,
    insert_arb_snapshot,
    prune_old_snapshots,
)
from .timeutil import now_utc_epoch, epoch_24h_ago
from .venues import VENUE_MAP, ALL_VENUES

_log = logging.getLogger(__name__)

_COLUMNS = [
    "rank",
    "exchange",
    "symbol",
    "funding_latest",
    "funding_avg_24h",
    "funding_window_hours",
    "perp_bid",
    "perp_bid_size_usdt",
    "spot_price",
    "basis_usd",
    "basis_bps",
    "est_gross_edge",
    "notes",
]


def run(
    top_n: int = 30,
    min_funding: float = 0.0,
    notional_usdt: float = 200.0,
    venues: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Fetch data, compute arb metrics, persist results.

    Returns:
        (DataFrame with _COLUMNS, status dict with last_refresh and partial_failures)
    """
    if venues is None:
        venues = ALL_VENUES

    ts_now = now_utc_epoch()
    ts_24h_ago = epoch_24h_ago()
    partial_failures: list[str] = []

    # ------------------------------------------------------------------ #
    # 1. Fetch funding rates + insert snapshots
    # ------------------------------------------------------------------ #
    conn = init_db(settings.DB_PATH)
    prune_old_snapshots(conn)

    raw_funding = loris.fetch_funding()
    for row in raw_funding:
        if row["exchange"] in venues:
            insert_funding_snapshot(conn, ts_now, row["exchange"], row["symbol"], row["funding"])

    # Build latest funding lookup: (exchange, symbol) -> funding_latest
    latest: dict[tuple[str, str], float] = {}
    for row in raw_funding:
        if row["exchange"] in venues:
            key = (row["exchange"], row["symbol"])
            latest[key] = row["funding"]

    # ------------------------------------------------------------------ #
    # 2. Compute rolling averages + filter by min_funding
    # ------------------------------------------------------------------ #
    candidates: list[dict] = []
    for (exchange, symbol), funding_latest in latest.items():
        avg, window_hours = get_rolling_avg(conn, exchange, symbol, ts_24h_ago)
        effective_avg = avg if avg is not None else funding_latest
        if effective_avg < min_funding:
            continue
        candidates.append(
            {
                "exchange": exchange,
                "symbol": symbol,
                "funding_latest": funding_latest,
                "funding_avg_24h": effective_avg,
                "funding_window_hours": window_hours,
                "avg_was_none": avg is None,
            }
        )

    # ------------------------------------------------------------------ #
    # 3. Parallel orderbook fetches
    # ------------------------------------------------------------------ #
    def _fetch_bid(cand: dict) -> dict:
        exchange = cand["exchange"]
        symbol = cand["symbol"]
        connector = VENUE_MAP.get(exchange)
        if connector is None:
            return {**cand, "perp_bid": None, "perp_bid_sz": None}
        bid, sz = connector(symbol)
        return {**cand, "perp_bid": bid, "perp_bid_sz": sz}

    enriched: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_bid, c): c for c in candidates}
        for fut in as_completed(futures):
            try:
                enriched.append(fut.result())
            except Exception as exc:
                cand = futures[fut]
                _log.warning("Bid fetch exception for %s/%s: %s", cand["exchange"], cand["symbol"], exc)
                partial_failures.append(f"{cand['exchange']}/{cand['symbol']}: {exc}")
                enriched.append({**cand, "perp_bid": None, "perp_bid_sz": None})

    # ------------------------------------------------------------------ #
    # 4. Spot prices
    # ------------------------------------------------------------------ #
    unique_symbols = list({r["symbol"] for r in enriched})
    spot_prices = dexscreener.fetch_spot_prices(unique_symbols, settings.spot_mapping)

    # ------------------------------------------------------------------ #
    # 5. Compute metrics
    # ------------------------------------------------------------------ #
    rows: list[dict] = []
    for r in enriched:
        symbol = r["symbol"]
        exchange = r["exchange"]
        perp_bid = r["perp_bid"]
        perp_bid_sz = r["perp_bid_sz"]
        spot = spot_prices.get(symbol)

        notes_parts: list[str] = []

        if perp_bid is None:
            notes_parts.append("orderbook fetch failed")

        if symbol not in settings.spot_mapping:
            notes_parts.append("spot mapping missing")

        if r["funding_window_hours"] < 24 and not r["avg_was_none"]:
            h = round(r["funding_window_hours"], 1)
            notes_parts.append(f"funding avg over {h}h only")
        elif r["avg_was_none"]:
            notes_parts.append("funding avg: single snapshot")

        # Compute derived metrics
        basis_usd: float | None = None
        basis_bps: float | None = None
        est_gross_edge: float | None = None
        perp_bid_size_usdt: float | None = None

        if perp_bid is not None and spot is not None and spot > 0:
            basis_usd = perp_bid - spot
            basis_bps = (basis_usd / spot) * 10_000
            est_gross_edge = basis_usd * (notional_usdt / spot)
            if perp_bid_sz is not None:
                perp_bid_size_usdt = perp_bid_sz * perp_bid

        rows.append(
            {
                "exchange": exchange,
                "symbol": symbol,
                "funding_latest": r["funding_latest"],
                "funding_avg_24h": r["funding_avg_24h"],
                "funding_window_hours": r["funding_window_hours"],
                "perp_bid": perp_bid,
                "perp_bid_size_usdt": perp_bid_size_usdt,
                "spot_price": spot,
                "basis_usd": basis_usd,
                "basis_bps": basis_bps,
                "est_gross_edge": est_gross_edge,
                "notes": "; ".join(notes_parts) if notes_parts else "",
            }
        )

    # ------------------------------------------------------------------ #
    # 6. Sort + rank + slice
    # ------------------------------------------------------------------ #
    if rows:
        df = pd.DataFrame(rows)
        df.sort_values(
            ["funding_avg_24h", "basis_bps"],
            ascending=[False, False],
            inplace=True,
            na_position="last",
        )
        df.reset_index(drop=True, inplace=True)
        df.insert(0, "rank", range(1, len(df) + 1))
        df = df.head(top_n)
    else:
        df = pd.DataFrame(columns=_COLUMNS)

    # ------------------------------------------------------------------ #
    # 7. Persist arb snapshot
    # ------------------------------------------------------------------ #
    insert_arb_snapshot(conn, ts_now, df.to_dict("records"))
    conn.close()

    status = {
        "last_refresh": ts_now,
        "partial_failures": partial_failures,
    }
    return df[_COLUMNS], status
