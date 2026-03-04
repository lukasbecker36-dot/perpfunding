"""Fetch spot prices from Dexscreener token API."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from . import http as _http

_log = logging.getLogger(__name__)

_BASE = "https://api.dexscreener.com/tokens/v1"
_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
_BATCH_SIZE = 30  # Dexscreener max addresses per request

_TARGET_CHAINS = {"bsc", "arbitrum", "base", "solana"}
_MIN_LIQUIDITY = 1_000_000  # $1M minimum

# Session-level cache: symbol -> {chain_id, token_address} or None
_discovered: dict[str, dict | None] = {}


def _pick_best_price(pairs: list[dict], token_address: str) -> float | None:
    """Return the USD price from the highest-liquidity pair for token_address.

    Pure function — importable without network calls.
    """
    token_addr_lower = token_address.lower()
    candidates: list[tuple[float, float]] = []  # (liquidity_usd, price_usd)

    for pair in pairs:
        # Match by base or quote token address
        base_addr = (pair.get("baseToken") or {}).get("address", "").lower()
        quote_addr = (pair.get("quoteToken") or {}).get("address", "").lower()
        if token_addr_lower not in (base_addr, quote_addr):
            continue

        price_str = pair.get("priceUsd") or ""
        if not price_str or price_str == "0":
            continue

        try:
            price = float(price_str)
        except ValueError:
            continue

        liq = (pair.get("liquidity") or {}).get("usd") or 0.0
        try:
            liq = float(liq)
        except (TypeError, ValueError):
            liq = 0.0

        candidates.append((liq, price))

    if not candidates:
        return None

    # Pick highest liquidity
    _, best_price = max(candidates, key=lambda x: x[0])
    return best_price


def _search_token(symbol: str) -> dict | None:
    """Search Dexscreener for a token by symbol.

    Returns {chain_id, token_address, price_usd} if found on target chains
    with sufficient liquidity, else None.
    """
    try:
        resp = _http.get(_SEARCH_URL, params={"q": symbol})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.debug("Dexscreener search failed for %s: %s", symbol, exc)
        return None

    pairs = data.get("pairs") or []
    if not isinstance(pairs, list):
        return None

    # Filter: exact symbol match on base token, target chains, min liquidity
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
        candidates.append({
            "chain_id": chain,
            "token_address": base.get("address", ""),
            "price_usd": price,
            "liquidity": liq,
        })

    if not candidates:
        return None

    # Pick highest liquidity
    best = max(candidates, key=lambda c: c["liquidity"])
    _log.debug(
        "Discovered %s on %s: addr=%s, liq=$%s",
        symbol, best["chain_id"], best["token_address"][:20], f"{best['liquidity']:,.0f}",
    )
    return best


def _discover_missing(symbols: list[str], spot_mapping: dict) -> dict[str, dict | None]:
    """Auto-discover token addresses for symbols not in spot_mapping.

    Returns {symbol: {chain_id, token_address, price_usd}} for discovered tokens.
    Uses session cache to avoid repeat lookups.
    """
    missing = [s for s in symbols if s.upper() not in spot_mapping and s not in _discovered]

    for sym in missing:
        result = _search_token(sym)
        _discovered[sym] = result
        time.sleep(0.25)  # Rate limit: ~4 req/sec

    return _discovered


def fetch_spot_prices(
    symbols: list[str],
    spot_mapping: dict,
) -> dict[str, float | None]:
    """Return {symbol: price_usd} for all requested symbols.

    For symbols in spot_mapping: uses the token-by-address endpoint (batched).
    For symbols NOT in spot_mapping: auto-discovers via Dexscreener search,
    filtering to BSC/Arbitrum/Base/Solana with >= $1M liquidity.
    """
    result: dict[str, float | None] = {s: None for s in symbols}

    # --- Phase 1: symbols with explicit mapping (batched token lookup) ---
    by_chain: dict[str, list[str]] = defaultdict(list)
    unmapped: list[str] = []

    for sym in symbols:
        mapping = spot_mapping.get(sym.upper())
        if mapping:
            by_chain[mapping["chain_id"]].append(sym)
        else:
            unmapped.append(sym)

    for chain_id, chain_syms in by_chain.items():
        for i in range(0, len(chain_syms), _BATCH_SIZE):
            batch = chain_syms[i : i + _BATCH_SIZE]
            addresses = [spot_mapping[s.upper()]["token_address"] for s in batch]
            addr_str = ",".join(addresses)
            url = f"{_BASE}/{chain_id}/{addr_str}"

            try:
                resp = _http.get(url)
                resp.raise_for_status()
                pairs: list[dict] = resp.json()
                if not isinstance(pairs, list):
                    pairs = (pairs or {}).get("pairs", [])
            except Exception as exc:
                _log.warning("Dexscreener fetch failed for %s: %s", chain_id, exc)
                continue

            for sym in batch:
                token_address = spot_mapping[sym.upper()]["token_address"]
                price = _pick_best_price(pairs, token_address)
                result[sym] = price
                if price is None:
                    _log.debug("No price found for %s (%s)", sym, token_address)

    # --- Phase 2: auto-discover unmapped symbols via search ---
    if unmapped:
        _discover_missing(unmapped, spot_mapping)
        for sym in unmapped:
            discovered = _discovered.get(sym)
            if discovered and discovered.get("price_usd"):
                result[sym] = discovered["price_usd"]

    return result
