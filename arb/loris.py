"""Fetch perpetual funding rates from Loris Tools."""
from __future__ import annotations

import logging

from . import http as _http
from .config import settings

_log = logging.getLogger(__name__)

_TARGET_EXCHANGES = {"hyperliquid", "kucoin", "aster", "edgex"}


def fetch_funding() -> list[dict]:
    """Return a list of funding rate dicts from Loris Tools.

    Each dict has keys: exchange (str), symbol (str), funding (float, in %).
    Returns [] on any HTTP error.

    API response shape:
        {
          "funding_rates": {
            "hyperliquid": {"BTC": 0.3, "ETH": 0.9, ...},
            "kucoin":      {"BTC": -0.3, ...},
            ...
          },
          ...
        }
    Symbols are already clean uppercase (no suffix).
    Rates are in percentage units (0.3 = 0.3% per funding period).
    """
    try:
        resp = _http.get(settings.LORIS_URL)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.warning("Loris fetch failed: %s", exc)
        return []

    funding_rates = data.get("funding_rates", {})
    if not isinstance(funding_rates, dict):
        _log.warning("Unexpected Loris response shape: %s", type(funding_rates))
        return []

    results: list[dict] = []
    for exchange, symbols in funding_rates.items():
        if exchange.lower() not in _TARGET_EXCHANGES:
            continue
        if not isinstance(symbols, dict):
            continue
        for symbol, rate in symbols.items():
            try:
                funding = float(rate)
            except (TypeError, ValueError):
                continue
            results.append(
                {
                    "exchange": exchange.lower(),
                    "symbol": symbol.upper(),
                    "funding": funding,
                }
            )

    _log.debug("Loris returned %d rows for target exchanges", len(results))
    return results
