"""Aster.ag perpetual futures orderbook connector."""
from __future__ import annotations

import logging

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
