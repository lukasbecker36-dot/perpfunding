"""edgeX perpetual futures orderbook connector.

Uses the public WebSocket API (REST is Cloudflare-blocked).
On first use, fetches contract metadata to build symbol -> contractId map.
Then subscribes to depth channels to get best bid/ask.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import Future

_log = logging.getLogger(__name__)

_WS_URL = "wss://quote.edgex.exchange/api/v1/public/ws"
_WS_TIMEOUT = 8  # seconds per operation

# Cached metadata: symbol (e.g. "BTC") -> contractId (e.g. "10000001")
_contract_map: dict[str, str] = {}
_contract_map_lock = threading.Lock()
_contract_map_loaded = False


def _parse_symbol_from_contract_name(name: str) -> str:
    """Extract clean symbol from edgeX contract name.

    edgeX names: BTCUSD, ETHUSD, 1000BONKUSD, BNB2USD, LTC2USD, AAPLUSD
    Loris symbols: BTC, ETH, BONK, BNB, LTC, AAPL

    Rules:
    - Strip trailing 'USD'
    - Strip leading '1000' multiplier prefix
    - Strip trailing '2' suffix (BNB2 -> BNB, LTC2 -> LTC, etc.)
    """
    s = name.upper()
    if s.endswith("USD"):
        s = s[:-3]
    if s.startswith("1000"):
        s = s[4:]
    # Some symbols have a '2' suffix (BNB2, LTC2, LINK2, etc.)
    # Only strip if the base without '2' is a common pattern
    if s.endswith("2") and len(s) > 2:
        s = s[:-1]
    return s


async def _fetch_metadata() -> dict[str, str]:
    """Connect to WS, subscribe to metadata, return symbol -> contractId map."""
    import websockets

    mapping: dict[str, str] = {}
    try:
        async with asyncio.timeout(_WS_TIMEOUT):
            async with websockets.connect(_WS_URL, open_timeout=5, max_size=2**22) as ws:
                await ws.recv()  # connected message
                await ws.send(json.dumps({"type": "subscribe", "channel": "metadata"}))

                for _ in range(10):
                    raw = await asyncio.wait_for(ws.recv(), timeout=_WS_TIMEOUT)
                    data = json.loads(raw)
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong", "time": data.get("time")}))
                        continue
                    if data.get("type") == "subscribed":
                        continue
                    # Metadata payload
                    content = data.get("content", {})
                    meta_items = content.get("data", [])
                    if meta_items and isinstance(meta_items[0], dict):
                        contracts = meta_items[0].get("contractList", [])
                        for c in contracts:
                            cid = c.get("contractId", "")
                            cname = c.get("contractName", "")
                            if cid and cname:
                                sym = _parse_symbol_from_contract_name(cname)
                                # Keep both the raw name mapping and clean symbol mapping
                                # If duplicate symbols, prefer the one without '2' suffix
                                if sym not in mapping:
                                    mapping[sym] = str(cid)
                    break
    except Exception as exc:
        _log.warning("edgeX metadata fetch failed: %s", exc)

    _log.debug("edgeX: loaded %d contract mappings", len(mapping))
    return mapping


def _ensure_contract_map() -> dict[str, str]:
    """Load contract map if not already loaded (thread-safe)."""
    global _contract_map, _contract_map_loaded
    if _contract_map_loaded:
        return _contract_map

    with _contract_map_lock:
        if _contract_map_loaded:
            return _contract_map

        def _run():
            return asyncio.run(_fetch_metadata())

        t = threading.Thread(target=lambda: None, daemon=True)
        result: list[dict] = [{}]

        def _target():
            result[0] = asyncio.run(_fetch_metadata())

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=_WS_TIMEOUT + 2)

        _contract_map = result[0]
        _contract_map_loaded = bool(_contract_map)
        return _contract_map


async def _fetch_best_bid(contract_id: str) -> tuple[float, float] | tuple[None, None]:
    """Subscribe to depth for a single contract, get snapshot, return best bid."""
    import websockets

    channel = f"depth.{contract_id}.15"
    try:
        async with asyncio.timeout(5):
            async with websockets.connect(_WS_URL, open_timeout=3, max_size=2**22) as ws:
                await ws.recv()  # connected
                await ws.send(json.dumps({"type": "subscribe", "channel": channel}))

                for _ in range(10):
                    raw = await asyncio.wait_for(ws.recv(), timeout=4)
                    data = json.loads(raw)
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong", "time": data.get("time")}))
                        continue
                    if data.get("type") == "subscribed":
                        continue

                    content = data.get("content", {})
                    items = content.get("data", [])
                    if items and isinstance(items, list) and isinstance(items[0], dict):
                        bids = items[0].get("bids", [])
                        if bids:
                            return float(bids[0]["price"]), float(bids[0]["size"])
                    # Snapshot received but empty orderbook
                    return None, None
    except Exception as exc:
        _log.debug("edgeX depth fetch failed for contract %s: %s", contract_id, exc)

    return None, None


async def _fetch_best_bids_batch(contract_ids: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Fetch best bids for multiple contracts on a single WS connection.

    contract_ids: {symbol: contractId}
    Returns: {symbol: (price, size)}
    """
    import websockets

    results: dict[str, tuple[float, float]] = {}
    # Reverse map: contractId -> symbol
    cid_to_sym = {cid: sym for sym, cid in contract_ids.items()}
    pending = set(contract_ids.values())

    try:
        total_timeout = max(15, len(contract_ids) * 0.3 + 10)
        async with asyncio.timeout(total_timeout):
            async with websockets.connect(_WS_URL, open_timeout=5, max_size=2**22) as ws:
                await ws.recv()  # connected

                # Subscribe to all depth channels
                for cid in contract_ids.values():
                    await ws.send(json.dumps({"type": "subscribe", "channel": f"depth.{cid}.15"}))

                deadline = asyncio.get_event_loop().time() + total_timeout - 2
                while pending:
                    try:
                        remaining = max(0.5, deadline - asyncio.get_event_loop().time())
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(5, remaining))
                    except asyncio.TimeoutError:
                        break
                    data = json.loads(raw)
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong", "time": data.get("time")}))
                        continue
                    if data.get("type") == "subscribed":
                        continue

                    content = data.get("content", {})
                    items = content.get("data", [])
                    if items and isinstance(items, list) and isinstance(items[0], dict):
                        book = items[0]
                        cid = book.get("contractId", "")
                        bids = book.get("bids", [])
                        if cid in pending:
                            if bids:
                                sym = cid_to_sym.get(cid)
                                if sym:
                                    results[sym] = (float(bids[0]["price"]), float(bids[0]["size"]))
                            # Remove from pending even if empty (no bids = no liquidity)
                            pending.discard(cid)
    except Exception as exc:
        _log.warning("edgeX batch depth fetch error: %s", exc)

    return results


# Module-level cache for batch results
_batch_cache: dict[str, tuple[float, float]] = {}
_batch_attempted: set[str] = set()  # symbols attempted in batch (even if no bids)
_batch_cache_ts: float = 0
_BATCH_TTL = 30  # seconds


_BATCH_CHUNK = 50  # max symbols per WS connection


def _refresh_batch_cache(symbols: list[str] | None = None) -> None:
    """Fetch best bids for edgeX symbols, chunked across parallel WS connections."""
    global _batch_cache, _batch_cache_ts
    import time

    cmap = _ensure_contract_map()
    if not cmap:
        return

    if symbols:
        needed = {s: cmap[s] for s in symbols if s in cmap}
    else:
        needed = cmap

    if not needed:
        return

    # Split into chunks and fetch in parallel threads
    items = list(needed.items())
    chunks = [dict(items[i:i + _BATCH_CHUNK]) for i in range(0, len(items), _BATCH_CHUNK)]

    all_results: list[dict] = [{} for _ in chunks]

    def _target(idx: int, chunk: dict) -> None:
        all_results[idx] = asyncio.run(_fetch_best_bids_batch(chunk))

    threads = []
    for idx, chunk in enumerate(chunks):
        t = threading.Thread(target=_target, args=(idx, chunk), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=max(15, _BATCH_CHUNK * 0.3 + 12))

    total = 0
    for r in all_results:
        if r:
            _batch_cache.update(r)
            total += len(r)

    _batch_attempted.update(needed.keys())
    if total:
        _batch_cache_ts = time.time()
    _log.info("edgeX: refreshed %d/%d best bids", total, len(needed))


def get_best_bid(symbol: str) -> tuple[float, float] | tuple[None, None]:
    """Return (best_bid_price, best_bid_size) from edgeX for a symbol.

    Uses cached batch results if fresh, otherwise fetches individually.
    """
    import time

    # Try batch cache first
    if symbol in _batch_cache and (time.time() - _batch_cache_ts) < _BATCH_TTL:
        return _batch_cache[symbol]

    # If batch already attempted this symbol and found no bids, skip individual fetch
    if symbol in _batch_attempted and (time.time() - _batch_cache_ts) < _BATCH_TTL:
        return None, None

    # Individual fetch
    cmap = _ensure_contract_map()
    contract_id = cmap.get(symbol)
    if not contract_id:
        _log.debug("edgeX: no contract mapping for %s", symbol)
        return None, None

    result: list = [None, None]

    def _target():
        result[0], result[1] = asyncio.run(_fetch_best_bid(contract_id))

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=7)

    if result[0] is None:
        _log.debug("edgeX: no bid for %s (contract %s) — likely empty orderbook", symbol, contract_id)
    return result[0], result[1]


def prefetch_bids(symbols: list[str]) -> None:
    """Pre-fetch best bids for a list of symbols in a single batch WS call.

    Call this before individual get_best_bid() calls to populate the cache.
    """
    _refresh_batch_cache(symbols)
