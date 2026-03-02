"""Shared HTTP client with per-host concurrency limits and retry logic."""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import httpx

_log = logging.getLogger(__name__)

# Per-host concurrency limits
HOST_CONCURRENCY: dict[str, int] = {
    "dexscreener": 2,
    "kucoin": 3,
    "hyperliquid": 3,
    "asterdex": 3,
    "edgex": 2,
}
_DEFAULT_CONCURRENCY = 5

_semaphores: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
                    follow_redirects=True,
                )
    return _client


def _get_semaphore(url: str) -> threading.Semaphore:
    hostname = urlparse(url).hostname or ""
    # Match partial hostname
    key = next((k for k in HOST_CONCURRENCY if k in hostname), None)
    limit = HOST_CONCURRENCY.get(key, _DEFAULT_CONCURRENCY) if key else _DEFAULT_CONCURRENCY
    with _sem_lock:
        if hostname not in _semaphores:
            _semaphores[hostname] = threading.Semaphore(limit)
        return _semaphores[hostname]


def _should_retry(exc: Exception | None, status: int | None) -> bool:
    if exc is not None:
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
    return status in (429, 500, 502, 503, 504)


def get(url: str, **kwargs) -> httpx.Response:
    """GET with retry and per-host concurrency control."""
    delays = [0.5, 1.0, 2.0]
    sem = _get_semaphore(url)
    last_exc: Exception | None = None
    with sem:
        for attempt, delay in enumerate(delays, 1):
            try:
                resp = _get_client().get(url, **kwargs)
                if _should_retry(None, resp.status_code):
                    _log.warning("GET %s → %s (attempt %d)", url, resp.status_code, attempt)
                    if attempt < len(delays):
                        time.sleep(delay)
                    continue
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                _log.warning("GET %s error %s (attempt %d)", url, exc, attempt)
                if attempt < len(delays):
                    time.sleep(delay)
    raise last_exc or httpx.RequestError(f"GET {url} failed after retries")


def post(url: str, json: dict, **kwargs) -> httpx.Response:
    """POST with retry and per-host concurrency control."""
    delays = [0.5, 1.0, 2.0]
    sem = _get_semaphore(url)
    last_exc: Exception | None = None
    with sem:
        for attempt, delay in enumerate(delays, 1):
            try:
                resp = _get_client().post(url, json=json, **kwargs)
                if _should_retry(None, resp.status_code):
                    _log.warning("POST %s → %s (attempt %d)", url, resp.status_code, attempt)
                    if attempt < len(delays):
                        time.sleep(delay)
                    continue
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                _log.warning("POST %s error %s (attempt %d)", url, exc, attempt)
                if attempt < len(delays):
                    time.sleep(delay)
    raise last_exc or httpx.RequestError(f"POST {url} failed after retries")
