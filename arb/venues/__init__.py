"""Venue connector registry and Protocol definition."""
from __future__ import annotations

from typing import Protocol

from .aster import fetch_funding_history as aster_history
from .aster import get_best_bid as aster_bid
from .edgex import fetch_funding_history as edgex_history
from .edgex import get_best_bid as edgex_bid
from .hyperliquid import fetch_funding_history as hl_history
from .hyperliquid import get_best_bid as hl_bid
from .kucoin import fetch_funding_history as kucoin_history
from .kucoin import get_best_bid as kucoin_bid


class VenueConnector(Protocol):
    def __call__(self, symbol: str) -> tuple[float, float] | tuple[None, None]: ...


VENUE_MAP: dict[str, VenueConnector] = {
    "hyperliquid": hl_bid,
    "kucoin": kucoin_bid,
    "aster": aster_bid,
    "edgex": edgex_bid,
}

ALL_VENUES: list[str] = list(VENUE_MAP.keys())

# Registry for historical funding rate fetchers
# Each function signature: (symbols: list[str], since_ts: int) -> list[tuple[int, str, float]]
HISTORY_MAP = {
    "hyperliquid": hl_history,
    "kucoin": kucoin_history,
    "aster": aster_history,
    "edgex": edgex_history,
}
