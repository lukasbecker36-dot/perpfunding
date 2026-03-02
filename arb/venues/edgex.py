"""edgeX perpetual futures orderbook connector.

Uses WebSocket for primary data; falls back to REST.
Always runs async code in a ThreadPoolExecutor to avoid Streamlit event loop conflicts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from .. import http as _http

_log = logging.getLogger(__name__)

_WS_URL = "wss://quote.edgex.exchange"
_REST_URL = "https://pro.edgex.exchange/api/v1/orderbook/{symbol}"

# Single-worker pool ensures each call gets its own thread/event loop
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edgex-ws")


async def _ws_get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Connect to edgeX WS, subscribe to orderbook, read first snapshot."""
    import websockets  # optional dep; imported lazily

    subscribe_msg = json.dumps({
        "type": "subscribe",
        "channel": "orderbook",
        "symbol": symbol,
    })

    try:
        async with websockets.connect(_WS_URL, open_timeout=10) as ws:
            await ws.send(subscribe_msg)
            # Read messages until we get an orderbook snapshot
            async with asyncio.timeout(10):
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    msg_type = msg.get("type") or msg.get("channel") or ""
                    # Accept snapshot or orderbook update with bids
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

    return None, None


def _run_ws_in_thread(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Run the async WS call in a fresh event loop inside a dedicated thread."""
    future = _executor.submit(_thread_target, symbol)
    try:
        return future.result(timeout=15)
    except Exception as exc:
        _log.debug("edgeX executor error for %s: %s", symbol, exc)
        return None, None


def _thread_target(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Executed in worker thread — safe to call asyncio.run()."""
    return asyncio.run(_ws_get_best_bid(symbol))


def _rest_fallback(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """REST fallback for edgeX orderbook."""
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
        _log.warning("edgeX REST fallback failed for %s: %s", symbol, exc)
    return None, None


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_size) from edgeX.

    Tries WebSocket first, falls back to REST.
    """
    result = _run_ws_in_thread(symbol)
    if result[0] is not None:
        return result

    _log.info("edgeX WS failed for %s, trying REST", symbol)
    result = _rest_fallback(symbol)
    if result[0] is None:
        _log.warning("edgeX: all methods failed for %s", symbol)
    return result
