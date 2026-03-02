"""KuCoin futures orderbook connector with contract multiplier support."""
from __future__ import annotations

import logging
import threading

from .. import http as _http

_log = logging.getLogger(__name__)

_TICKER_URL = "https://api-futures.kucoin.com/api/v1/ticker"
_CONTRACT_URL = "https://api-futures.kucoin.com/api/v1/contracts/{sym}"
_DEPTH_URL = "https://api-futures.kucoin.com/api/v1/level2/snapshot"

# Cache multipliers for the session to avoid repeated API calls
_contract_cache: dict[str, float] = {}
_cache_lock = threading.Lock()


def _kucoin_symbol(symbol: str) -> str:
    """Convert normalized symbol to KuCoin futures format."""
    if symbol.upper() in ("BTC", "XBT"):
        return "XBTUSDTM"
    return f"{symbol.upper()}USDTM"


def _get_multiplier(kc_sym: str) -> float:
    """Return the contract multiplier for a KuCoin futures symbol.

    Fetches once and caches. Returns 1.0 on failure.
    """
    with _cache_lock:
        if kc_sym in _contract_cache:
            return _contract_cache[kc_sym]

    try:
        url = _CONTRACT_URL.format(sym=kc_sym)
        resp = _http.get(url)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        mult = float(data.get("multiplier") or 1.0)
    except Exception as exc:
        _log.warning("KuCoin contract multiplier fetch failed for %s: %s", kc_sym, exc)
        mult = 1.0

    with _cache_lock:
        _contract_cache[kc_sym] = mult
    return mult


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_size_base) from KuCoin futures.

    size_base = best_bid_size_lots * contract_multiplier
    """
    kc_sym = _kucoin_symbol(symbol)
    multiplier = _get_multiplier(kc_sym)

    # Primary: ticker endpoint
    try:
        resp = _http.get(_TICKER_URL, params={"symbol": kc_sym})
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        bid_price = data.get("bestBidPrice")
        bid_size = data.get("bestBidSize")
        if bid_price and bid_size:
            price = float(bid_price)
            size_base = float(bid_size) * multiplier
            return price, size_base
    except Exception as exc:
        _log.debug("KuCoin ticker failed for %s: %s", kc_sym, exc)

    # Fallback: level2 snapshot
    try:
        resp = _http.get(_DEPTH_URL, params={"symbol": kc_sym})
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        bids = data.get("bids", [])
        if bids:
            price = float(bids[0][0])
            size_base = float(bids[0][1]) * multiplier
            return price, size_base
    except Exception as exc:
        _log.warning("KuCoin depth fallback failed for %s: %s", kc_sym, exc)

    return None, None
