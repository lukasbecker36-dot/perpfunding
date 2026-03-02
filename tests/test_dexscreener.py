"""Tests for dexscreener._pick_best_price — no network calls."""
import json
from pathlib import Path

import pytest

from arb.dexscreener import _pick_best_price

_FIXTURES = Path(__file__).parent / "fixtures" / "dexscreener_sample.json"


@pytest.fixture
def sample_pairs():
    with _FIXTURES.open() as f:
        return json.load(f)


WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
UNKNOWN = "0xdeadbeef00000000000000000000000000000000"


def test_picks_highest_liquidity(sample_pairs):
    """Should return price from the pair with highest liquidity.usd."""
    price = _pick_best_price(sample_pairs, WBTC)
    # Pair with $5M liquidity has priceUsd=65432.10; $1M pair has 65400.00
    assert price is not None
    assert abs(price - 65432.10) < 0.01


def test_zero_price_returns_none(sample_pairs):
    """Pairs with priceUsd='0' must be skipped; return None if no valid price."""
    price = _pick_best_price(sample_pairs, WETH)
    # Only WETH pair has priceUsd="0" — should return None
    assert price is None


def test_missing_token_returns_none(sample_pairs):
    """Return None when token address not found in any pair."""
    price = _pick_best_price(sample_pairs, UNKNOWN)
    assert price is None
