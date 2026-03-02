# Crypto Spot-Perp Arbitrage Dashboard

Scans 4 crypto exchanges (KuCoin, Aster, edgeX, Hyperliquid) for spot-perp funding arbitrage opportunities.

Funding rates sourced from [Loris Tools](https://loris.tools). Spot prices from [Dexscreener](https://dexscreener.com).

---

## Features

- **24h rolling funding averages** accumulated in SQLite
- **Live orderbook best bids** from each perpetual venue
- **Spot prices** via Dexscreener token API (batched)
- **Basis / edge metrics**: `basis_usd`, `basis_bps`, `est_gross_edge`
- **CLI** with rich color output + CSV export
- **Streamlit dashboard** with live sorting and download

---

## Local Setup

### Prerequisites

- Python 3.11+

### Install

```bash
cd perpfunding
pip install -e ".[dev]"
```

### Environment

```bash
cp .env.example .env
# Edit .env if you have KuCoin API keys (optional)
```

---

## CLI Usage

```bash
# Basic run — top 30 opportunities
python -m arb.cli run

# Custom filters
python -m arb.cli run --top 20 --min-funding 0.0005 --notional 500

# Specific venues only
python -m arb.cli run --venues kucoin hyperliquid

# All options
python -m arb.cli run --help
```

Output is printed to stdout (rich table) and saved to `out/latest.csv`.
History accumulates in `out/history.sqlite`.

### Accumulation example

Run the CLI twice to accumulate data and widen the rolling average window:

```bash
python -m arb.cli run
sleep 300
python -m arb.cli run
# funding_window_hours will now show ~0.08h (5 min)
```

---

## Streamlit Dashboard

### Local

```bash
streamlit run streamlit_app.py
```

Open [http://localhost:8501](http://localhost:8501), adjust sidebar settings, click **Refresh**.

### Streamlit Community Cloud

1. Push this repo to GitHub (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Select repo, branch, set main file to `streamlit_app.py`
4. **Secrets** (optional, for KuCoin auth):
   - In the app settings → Secrets:
   ```toml
   KUCOIN_API_KEY = "your_key"
   KUCOIN_API_SECRET = "your_secret"
   KUCOIN_API_PASSPHRASE = "your_passphrase"
   ```

---

## Running Tests

```bash
pytest tests/ -v
```

All 6 tests run without network calls.

---

## Adding Symbols

Edit `config/spot_mapping.yaml`:

```yaml
MYTOKEN:
  chain_id: "ethereum"
  token_address: "0xYourTokenAddressHere"
```

Symbols are automatically included in Dexscreener batch fetches (≤30 addresses per chain per request).

---

## Architecture

```
arb/
├── config.py       # Settings, spot_mapping, normalize_symbol()
├── http.py         # Shared httpx client, retries, per-host semaphores
├── timeutil.py     # UTC epoch helpers
├── storage.py      # SQLite WAL, funding + arb snapshots
├── loris.py        # Loris Tools funding API
├── dexscreener.py  # Dexscreener spot prices
├── core.py         # Orchestration — returns (DataFrame, status)
├── cli.py          # argparse + rich table CLI
└── venues/
    ├── hyperliquid.py  # POST l2Book
    ├── kucoin.py       # Ticker + contract multiplier cache
    ├── aster.py        # bookTicker / depth
    └── edgex.py        # WebSocket (ThreadPoolExecutor) + REST fallback
```

### Key design notes

- **SQLite WAL mode** — safe for concurrent CLI + Streamlit access
- **edgeX async isolation** — `asyncio.run()` always in a `ThreadPoolExecutor` thread to avoid Streamlit event loop conflicts
- **KuCoin multipliers** — fetched once per session and cached; contracts are sized in lots, not base units
- **Dexscreener batching** — up to 30 addresses per chain per request; grouped by `chain_id`
- **Partial failures** — venue/spot errors are reported in `notes` column and status dict; the run always completes

---

## Rate Limits

| Service | Limit | Mitigation |
|---------|-------|-----------|
| Dexscreener | ~10 req/min | Batch 30 addrs/request, per-host semaphore (2 concurrent) |
| KuCoin | ~30 req/10s public | Semaphore (3 concurrent), exponential backoff on 429 |
| Hyperliquid | Generous | Semaphore (3 concurrent) |
| Aster | Unknown | Semaphore (3 concurrent), fallback to depth |
| edgeX | WS per-sub | 10s timeout, REST fallback |

---

## Attribution

Funding rate data provided by [Loris Tools](https://loris.tools)
