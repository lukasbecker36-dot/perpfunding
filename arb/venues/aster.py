"""Aster.ag perpetual futures orderbook connector."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import http as _http

_log = logging.getLogger(__name__)

_BOOK_TICKER_URL = "https://fapi.asterdex.com/fapi/v1/ticker/bookTicker"
_DEPTH_URL = "https://fapi.asterdex.com/fapi/v1/depth"


def _aster_symbol(symbol: str) -> str:
    return f"{symbol.upper()}USDT"


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_qty) from Aster futures."""
    sym = _aster_symbol(symbol)

    # Primary: bookTicker
    try:
        resp = _http.get(_BOOK_TICKER_URL, params={"symbol": sym})
        if resp.status_code == 400:
            return None, None  # symbol not listed on Aster
        resp.raise_for_status()
        data = resp.json()
        # Response may be a list or a single dict
        if isinstance(data, list):
            data = next((d for d in data if d.get("symbol") == sym), {})
        bid_price = data.get("bidPrice")
        bid_qty = data.get("bidQty")
        if bid_price and bid_qty:
            return float(bid_price), float(bid_qty)
    except Exception as exc:
        _log.debug("Aster bookTicker failed for %s: %s", sym, exc)

    # Fallback: depth endpoint (only reached on 5xx / parse errors, not 400)
    try:
        resp = _http.get(_DEPTH_URL, params={"symbol": sym, "limit": 5})
        if resp.status_code == 400:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        bids = data.get("bids", [])
        if bids:
            return float(bids[0][0]), float(bids[0][1])
    except Exception as exc:
        _log.debug("Aster depth fallback failed for %s: %s", sym, exc)

    return None, None


_FUNDING_RATE_URL = "https://fapi.asterdex.com/fapi/v1/fundingRate"


def fetch_funding_history(symbols: list[str], since_ts: int) -> list[tuple[int, str, float]]:
    """Fetch historical funding rates from Aster.

    Returns list of (ts_seconds, symbol, funding_rate_pct) tuples.
    """
    since_ms = since_ts * 1000
    results: list[tuple[int, str, float]] = []

    def _fetch_one(symbol: str) -> list[tuple[int, str, float]]:
        sym = _aster_symbol(symbol)
        try:
            resp = _http.get(_FUNDING_RATE_URL, params={
                "symbol": sym,
                "startTime": since_ms,
                "limit": 1000,
            })
            if resp.status_code == 400:
                return []
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return []
            rows = []
            for rec in data:
                ts = int(rec.get("fundingTime", 0)) // 1000
                rate = float(rec.get("fundingRate", 0)) * 100 * 3 * 365  # decimal -> annualized %
                if ts > 0:
                    rows.append((ts, symbol, rate))
            return rows
        except Exception as exc:
            _log.warning("Aster funding history failed for %s: %s", symbol, exc)
            return []

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futs):
            results.extend(fut.result())

    _log.info("Aster: fetched %d historical funding rows", len(results))
    return results
