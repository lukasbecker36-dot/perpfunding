"""Fetch perpetual funding rates from Loris Tools."""
from __future__ import annotations

import logging

from . import http as _http
from .config import normalize_symbol, settings

_log = logging.getLogger(__name__)

# Canonical names for the 4 target exchanges
_TARGET_EXCHANGES = {"hyperliquid", "kucoin", "aster", "edgex"}

# Aliases the API might return
_EXCHANGE_ALIASES: dict[str, str] = {
    "hyperliquid": "hyperliquid",
    "hyperliq": "hyperliquid",
    "hl": "hyperliquid",
    "kucoin": "kucoin",
    "kucoin futures": "kucoin",
    "aster": "aster",
    "aster.ag": "aster",
    "edgex": "edgex",
    "edge x": "edgex",
    "edgex exchange": "edgex",
}


def _normalize_exchange(raw: str) -> str | None:
    """Map raw exchange name to canonical name, or None if not a target."""
    return _EXCHANGE_ALIASES.get(raw.strip().lower())


def fetch_funding() -> list[dict]:
    """Return a list of funding rate dicts from Loris Tools.

    Each dict has keys: exchange, symbol, funding (float, per-period rate).
    Returns [] on any HTTP error.
    """
    try:
        resp = _http.get(settings.LORIS_URL)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _log.warning("Loris fetch failed: %s", exc)
        return []

    # API may return a list or {data: [...]} envelope
    if isinstance(data, dict):
        data = data.get("data", [])

    results: list[dict] = []
    for item in data:
        raw_exchange = item.get("exchange", "")
        canonical = _normalize_exchange(raw_exchange)
        if canonical is None:
            continue

        raw_symbol = item.get("symbol", "")
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            continue

        try:
            funding = float(item.get("fundingRate") or item.get("funding") or 0)
        except (TypeError, ValueError):
            continue

        results.append(
            {
                "exchange": canonical,
                "symbol": symbol,
                "funding": funding,
            }
        )

    _log.debug("Loris returned %d rows (filtered to targets)", len(results))
    return results
