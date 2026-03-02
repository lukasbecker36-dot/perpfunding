"""Venue connector registry and Protocol definition."""
from __future__ import annotations

from typing import Protocol

from .aster import get_best_bid as aster_bid
from .edgex import get_best_bid as edgex_bid
from .hyperliquid import get_best_bid as hl_bid
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
