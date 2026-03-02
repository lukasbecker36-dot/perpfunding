"""edgeX perpetual futures orderbook connector.

REST is the primary method (fast, reliable).
WebSocket is the fallback for symbols not available via REST.
Async WS calls always run in a fresh thread to avoid Streamlit event loop conflicts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from .. import http as _http

_log = logging.getLogger(__name__)

_REST_URL = "https://pro.edgex.exchange/api/v1/orderbook/{symbol}"
_WS_URL = "wss://quote.edgex.exchange"

_WS_TIMEOUT = 6  # seconds — short so failures don't stall the whole run


def _rest_get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Primary: REST orderbook endpoint."""
    try:
        url = _REST_URL.format(symbol=symbol)
        resp = _http.get(url)
        resp.raise_for_status()
        data = resp.json()
        bids = data.get("bids") or (data.get("data") or {}).get("bids") or []
        if bids:
            best = bids[0]
            if isinstance(best, dict):
                return float(best.get("price") or best.get("px", 0)), float(
                    best.get("size") or best.get("sz", 0)
                )
            elif isinstance(best, (list, tuple)) and len(best) >= 2:
                return float(best[0]), float(best[1])
    except Exception as exc:
        _log.debug("edgeX REST failed for %s: %s", symbol, exc)
    return None, None


async def _ws_get_best_bid_async(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Fallback: WebSocket orderbook subscription."""
    import websockets

    subscribe_msg = json.dumps({
        "type": "subscribe",
        "channel": "orderbook",
        "symbol": symbol,
    })
    try:
        async with asyncio.timeout(_WS_TIMEOUT):
            async with websockets.connect(_WS_URL, open_timeout=_WS_TIMEOUT) as ws:
                await ws.send(subscribe_msg)
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    bids = (
                        msg.get("bids")
                        or (msg.get("data") or {}).get("bids")
                        or []
                    )
                    if bids:
                        best = bids[0]
                        if isinstance(best, dict):
                            return float(best.get("price") or best.get("px", 0)), float(
                                best.get("size") or best.get("sz", 0)
                            )
                        elif isinstance(best, (list, tuple)) and len(best) >= 2:
                            return float(best[0]), float(best[1])
    except Exception as exc:
        _log.debug("edgeX WS failed for %s: %s", symbol, exc)
    return None, None


def _ws_in_thread(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Run async WS call in a dedicated thread with its own event loop."""
    result: list = [None, None]

    def _target():
        result[0], result[1] = asyncio.run(_ws_get_best_bid_async(symbol))

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=_WS_TIMEOUT + 2)
    if t.is_alive():
        _log.debug("edgeX WS thread timed out for %s", symbol)
        return None, None
    return result[0], result[1]


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_size) from edgeX.

    Tries REST first (fast), falls back to WebSocket.
    """
    price, size = _rest_get_best_bid(symbol)
    if price is not None:
        return price, size

    _log.info("edgeX REST failed for %s, trying WS", symbol)
    price, size = _ws_in_thread(symbol)
    if price is None:
        _log.warning("edgeX: all methods failed for %s", symbol)
    return price, size
