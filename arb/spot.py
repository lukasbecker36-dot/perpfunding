"""Fetch spot prices: KuCoin spot orderbook (primary), Dexscreener (fallback).

For KuCoin-listed spot pairs: uses allTickers for best ask (the price you'd
pay to buy spot) in a single API call.

For tokens without a KuCoin spot pair (Alpha/DeFi tokens): falls back to
Dexscreener search, filtering to BSC/Arbitrum/Base/Solana with >= $1M liquidity.
"""
from __future__ import annotations

import logging
import time

from . import http as _http

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KuCoin spot
# ---------------------------------------------------------------------------
_KUCOIN_ALL_TICKERS = "https://api.kucoin.com/api/v1/market/allTickers"

# Cached KuCoin spot data: {symbol: {ask, bid, last}}
_kucoin_spot: dict[str, dict] = {}
_kucoin_spot_ts: float = 0
_KUCOIN_TTL = 30  # seconds


def _refresh_kucoin_spot() -> None:
    """Fetch all KuCoin USDT spot tickers in one call and cache."""
    global _kucoin_spot, _kucoin_spot_ts

    if time.time() - _kucoin_spot_ts < _KUCOIN_TTL and _kucoin_spot:
        return

    try:
        resp = _http.get(_KUCOIN_ALL_TICKERS)
        resp.raise_for_status()
        tickers = resp.json().get("data", {}).get("ticker", [])
    except Exception as exc:
        _log.warning("KuCoin allTickers failed: %s", exc)
        return

    spot: dict[str, dict] = {}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("-USDT"):
            continue
        base = sym[:-5]
        try:
            ask = float(t["sell"]) if t.get("sell") else None
            bid = float(t["buy"]) if t.get("buy") else None
            last = float(t["last"]) if t.get("last") else None
        except (ValueError, TypeError):
            continue
        spot[base] = {"ask": ask, "bid": bid, "last": last}

    _kucoin_spot = spot
    _kucoin_spot_ts = time.time()
    _log.debug("KuCoin spot: loaded %d USDT pairs", len(spot))


def kucoin_spot_price(symbol: str) -> float | None:
    """Return the spot ask (buy) price from KuCoin, or None if not listed."""
    _refresh_kucoin_spot()
    entry = _kucoin_spot.get(symbol)
    if entry is None:
        return None
    # Prefer ask (what you'd pay to buy), fall back to last
    return entry.get("ask") or entry.get("last")


# ---------------------------------------------------------------------------
# Dexscreener fallback
# ---------------------------------------------------------------------------
_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
_TARGET_CHAINS = {"bsc", "arbitrum", "base", "solana"}
_MIN_LIQUIDITY = 1_000_000  # $1M

# Session cache: symbol -> price or None
_dex_cache: dict[str, float | None] = {}


def _dexscreener_price(symbol: str) -> float | None:
    """Search Dexscreener for a token, return price if found with sufficient liquidity."""
    if symbol in _dex_cache:
        return _dex_cache[symbol]

    try:
        resp = _http.get(_SEARCH_URL, params={"q": symbol})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.debug("Dexscreener search failed for %s: %s", symbol, exc)
        _dex_cache[symbol] = None
        return None

    pairs = data.get("pairs") or []
    if not isinstance(pairs, list):
        _dex_cache[symbol] = None
        return None

    candidates = []
    for pair in pairs:
        base = pair.get("baseToken")
        if not isinstance(base, dict):
            continue
        if base.get("symbol", "").upper() != symbol.upper():
            continue
        chain = pair.get("chainId", "")
        if chain not in _TARGET_CHAINS:
            continue
        liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
        if liq < _MIN_LIQUIDITY:
            continue
        price_str = pair.get("priceUsd") or ""
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            continue
        candidates.append((liq, price))

    if candidates:
        _, best_price = max(candidates, key=lambda c: c[0])
        _dex_cache[symbol] = best_price
        return best_price

    _dex_cache[symbol] = None
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_spot_prices(symbols: list[str]) -> dict[str, float | None]:
    """Return {symbol: spot_price_usd} for all requested symbols.

    Phase 1: KuCoin allTickers (single call, ~940 USDT pairs, orderbook best ask).
    Phase 2: Dexscreener search fallback for symbols not on KuCoin spot
             (filters to BSC/Arbitrum/Base/Solana, >= $1M liquidity).
    """
    result: dict[str, float | None] = {}

    # Phase 1: KuCoin spot
    _refresh_kucoin_spot()
    remaining: list[str] = []

    for sym in symbols:
        price = kucoin_spot_price(sym)
        if price is not None:
            result[sym] = price
        else:
            remaining.append(sym)

    # Phase 2: Dexscreener for the rest
    for sym in remaining:
        price = _dexscreener_price(sym)
        result[sym] = price
        if price is None:
            _log.debug("No spot price for %s (not on KuCoin spot, not on Dexscreener with >=$1M liq)", sym)
        time.sleep(0.25)  # Dexscreener rate limit

    found = sum(1 for v in result.values() if v is not None)
    _log.info("Spot prices: %d/%d found (KuCoin: %d, Dexscreener: %d)",
              found, len(symbols),
              sum(1 for s in symbols if s not in remaining and result.get(s) is not None),
              sum(1 for s in remaining if result.get(s) is not None))

    return result
