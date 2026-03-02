"""Fetch spot prices from Dexscreener token API."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from . import http as _http

_log = logging.getLogger(__name__)

_BASE = "https://api.dexscreener.com/tokens/v1"
_BATCH_SIZE = 30  # Dexscreener max addresses per request


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


def fetch_spot_prices(
    symbols: list[str],
    spot_mapping: dict,
) -> dict[str, float | None]:
    """Return {symbol: price_usd} for all requested symbols.

    Groups by chain_id and batches up to 30 addresses per request.
    Symbols not in spot_mapping → None.
    """
    result: dict[str, float | None] = {s: None for s in symbols}

    # Group symbols by chain_id
    by_chain: dict[str, list[str]] = defaultdict(list)
    for sym in symbols:
        mapping = spot_mapping.get(sym.upper())
        if mapping:
            by_chain[mapping["chain_id"]].append(sym)
        else:
            _log.debug("No spot mapping for %s", sym)

    for chain_id, chain_syms in by_chain.items():
        # Batch into groups of BATCH_SIZE
        for i in range(0, len(chain_syms), _BATCH_SIZE):
            batch = chain_syms[i : i + _BATCH_SIZE]
            addresses = [spot_mapping[s]["token_address"] for s in batch]
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
                token_address = spot_mapping[sym]["token_address"]
                price = _pick_best_price(pairs, token_address)
                result[sym] = price
                if price is None:
                    _log.debug("No price found for %s (%s)", sym, token_address)

    return result
