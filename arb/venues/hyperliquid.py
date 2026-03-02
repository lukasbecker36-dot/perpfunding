"""Hyperliquid perpetual orderbook connector."""
from __future__ import annotations

import logging

from .. import http as _http

_log = logging.getLogger(__name__)

_URL = "https://api.hyperliquid.xyz/info"


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
        _log.warning("Hyperliquid error for %s: %s", symbol, data["error"])
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
