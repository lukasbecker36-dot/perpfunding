"""Hyperliquid perpetual orderbook connector."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import http as _http

_log = logging.getLogger(__name__)

_URL = "https://api.hyperliquid.xyz/info"


def fetch_funding_history(symbols: list[str], since_ts: int) -> list[tuple[int, str, float]]:
    """Fetch historical funding rates from Hyperliquid.

    Returns list of (ts_seconds, symbol, funding_rate_pct) tuples.
    """
    since_ms = since_ts * 1000
    results: list[tuple[int, str, float]] = []

    def _fetch_one(symbol: str) -> list[tuple[int, str, float]]:
        try:
            resp = _http.post(_URL, json={
                "type": "fundingHistory",
                "coin": symbol,
                "startTime": since_ms,
            })
            resp.raise_for_status()
            data = resp.json()
            rows = []
            for rec in data:
                ts = int(rec["time"]) // 1000  # ms -> s
                rate = float(rec["fundingRate"]) * 100  # decimal -> %
                rows.append((ts, symbol, rate))
            return rows
        except Exception as exc:
            _log.warning("Hyperliquid funding history failed for %s: %s", symbol, exc)
            return []

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futs):
            results.extend(fut.result())

    _log.info("Hyperliquid: fetched %d historical funding rows", len(results))
    return results


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_size) from Hyperliquid L2 book.

    symbol: normalized base asset, e.g. "BTC", "ETH".
    """
    try:
        resp = _http.post(_URL, json={"type": "l2Book", "coin": symbol})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.warning("Hyperliquid orderbook failed for %s: %s", symbol, exc)
        return None, None

    if "error" in data:
        _log.debug("Hyperliquid: symbol not found %s: %s", symbol, data["error"])
        return None, None

    try:
        levels = data["levels"]
        # levels[0] = bids, levels[1] = asks
        bids = levels[0]
        if not bids:
            return None, None
        best = bids[0]
        return float(best["px"]), float(best["sz"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        _log.warning("Hyperliquid parse error for %s: %s", symbol, exc)
        return None, None
