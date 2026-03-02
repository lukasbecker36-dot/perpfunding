# Project: Crypto Spot–Perp Arbitrage Dashboard (Funding + Basis + Depth)

## Goal
Build a Python project that:
1) Pulls **perp funding rates** from Loris Tools API for these venues: **KuCoin, Aster, edgeX, Hyperliquid**
2) Computes **24h average funding rate** per (exchange, symbol) and ranks **highest positive**.
3) For the top funding opportunities, fetches the **perp best bid (sell price)** and **available size in USDT** at that best bid.
4) For the corresponding spot token, fetches **spot price** from **Dexscreener** and computes the **basis** between:
   - perp sell price (best bid)
   - spot buy price (Dexscreener priceUsd)
   for a **200 USDT notional**.
5) Outputs a clean table (CSV + stdout).
6) Then build a **Streamlit app** that runs this remotely, shows the table, and lets me refresh on demand.

Important: implement robust error handling + rate limiting + caching. Keep secrets out of git.

---

## Constraints / Reality Checks (handle explicitly)
- Loris Tools public docs show **GET https://api.loris.tools/funding** returns the current snapshot (no params). Use it as the primary funding source. The docs also include a production-warning + attribution requirement; include attribution in the Streamlit UI footer. (Source: Loris Tools API docs.)
- 24h average funding:
  - Since Loris’ public endpoint is snapshot-style, implement a **local time-series store** (SQLite) that appends snapshots and computes rolling 24h averages.
  - If the database has <24h history for a symbol, compute average over available window and expose `window_hours` in output so I can see it’s not full 24h yet.
- Dexscreener:
  - Use official endpoints:
    - `GET https://api.dexscreener.com/tokens/v1/{chainId}/{tokenAddresses}` (batch up to 30)
    - or `GET https://api.dexscreener.com/token-pairs/v1/{chainId}/{tokenAddress}` if needed
  - We want `priceUsd` and optionally liquidity info to sanity-check.
- Orderbook / best bid:
  - Hyperliquid: use `POST https://api.hyperliquid.xyz/info` with body `{"type":"l2Book","coin":"ETH"}` to get L2 book; take best bid price + size.
  - KuCoin: use KuCoin futures/market endpoints to get best bid + size; prefer a lightweight endpoint (ticker/best bid) if available; fallback to orderbook snapshot if needed.
  - Aster: use their official API docs repo (GitHub) and implement a best-bid fetch via their depth endpoint (Binance-style `.../fapi/v1/depth` is common; confirm in their docs and implement accordingly).
  - edgeX: their docs show endpoint domain + websocket. Implement best bid via websocket subscription if REST is unclear; include a REST fallback if you find an orderbook endpoint in their docs.
- Symbol mapping spot<->perp:
  - Funding symbols are usually like `BTC`, `ETH`, etc; but Dexscreener needs (chainId, tokenAddress).
  - Implement a **config mapping file** where I define, per symbol, which chain + token address to use for spot pricing.
  - Provide an example mapping and make it easy to extend.

---

## Deliverables
### 1) Python package
Create repo structure:

```

arb_dashboard/
arb/
**init**.py
config.py
http.py
timeutil.py
storage.py
loris.py
dexscreener.py
venues/
**init**.py
kucoin.py
hyperliquid.py
aster.py
edgex.py
core.py
cli.py
streamlit_app.py
pyproject.toml
README.md
.env.example

````

### 2) CLI
`python -m arb.cli run --top 30 --min-funding 0.0005 --notional 200`

Outputs:
- table to stdout
- writes `out/latest.csv`
- writes sqlite db `out/history.sqlite`

### 3) Streamlit
`streamlit run streamlit_app.py`
Features:
- “Refresh” button
- Controls: `top_n`, `min_funding`, `notional_usdt`, venue filters
- Table with sortable columns
- Footer attribution: “Funding rate data provided by Loris Tools” (with link)
- A status area showing last refresh time and any partial failures

### 4) GitHub + Remote Run
Add README steps for:
- GitHub repo setup
- Streamlit Community Cloud deployment
- Setting secrets in Streamlit (API keys if needed)
- Notes on rate limits + caching

---

## Data Model / Output Columns
Each row is (exchange, symbol):

- `rank`
- `exchange`
- `symbol`
- `funding_avg_24h` (decimal, e.g. 0.0012 = 0.12% per funding interval as reported)
- `funding_window_hours` (how much history used)
- `funding_last` (latest snapshot value)
- `perp_best_bid_price`
- `perp_best_bid_size_base`
- `perp_best_bid_size_usdt` = price * size_base
- `spot_price_usd` (Dexscreener `priceUsd`)
- `basis_usd` = perp_best_bid_price - spot_price_usd
- `basis_bps` = basis_usd / spot_price_usd * 10_000
- `est_gross_edge_usdt_on_200` = (perp_best_bid_price - spot_price_usd) * (200 / spot_price_usd)
- `notes` (e.g. “spot mapping missing”, “<24h history”, “orderbook fetch failed”)

Sorting: descending `funding_avg_24h`, tie-break by `basis_bps` descending.

---

## Implementation Details (be strict)
### HTTP + retries
- Use `httpx` with timeouts, retries (exponential backoff), and a per-host rate limiter.
- Centralize in `arb/http.py`.

### Storage (SQLite)
- `out/history.sqlite`
- Table `funding_snapshots`:
  - `ts` (UTC ISO or epoch)
  - `exchange`
  - `symbol`
  - `funding` (REAL)
- Index on `(exchange, symbol, ts)`
- Function `get_rolling_avg(exchange, symbol, since_ts)`.

### Loris ingestion
- Call `GET https://api.loris.tools/funding` and parse into rows:
  - normalize exchange names to: `kucoin`, `aster`, `edgex`, `hyperliquid`
  - normalize symbol (uppercase)
- Insert into sqlite.

### Venue connectors
Each venue module exposes:
- `list_supported_symbols()` (optional; can be stubbed if not needed)
- `get_best_bid(symbol) -> (price, size_base)` with robust error handling.

Hyperliquid:
- `POST /info` l2Book
- Use best bid = first bids level

KuCoin:
- Use their documented futures market endpoints to fetch best bid and size.
- Keep it lightweight; don’t fetch full book unless necessary.

Aster:
- Use official docs in `asterdex/api-docs` (GitHub).
- Implement depth fetch and extract best bid.

edgeX:
- Docs show `https://pro.edgex.exchange` and `wss://quote.edgex.exchange`.
- Implement websocket client to subscribe to orderbook/ticker; extract best bid.
- If websocket is too involved, implement a minimal “connect once, read first snapshot, close” approach.

### Dexscreener spot pricing
- Config file `arb_config.yaml` or `config/spot_mapping.yaml`:
  - example:
    ```yaml
    spot_mapping:
      ETH:
        chainId: ethereum
        tokenAddress: 0x...
      SOL:
        chainId: solana
        tokenAddress: So11111111111111111111111111111111111111112
    ```
- Use batch endpoint `GET /tokens/v1/{chainId}/{tokenAddresses}` (up to 30).
- Choose a price:
  - prefer the pair with highest `liquidity.usd` if multiple returned
  - fallback to first valid `priceUsd`

### Basis calc
- Use `notional_usdt=200`
- `qty = notional_usdt / spot_price_usd`
- `est_gross_edge_usdt_on_200 = (perp_best_bid_price - spot_price_usd) * qty`

### Testing
- Add a couple of unit tests:
  - rolling avg computation
  - dexscreener “pick best liquidity” logic
- Keep tests deterministic with sample JSON fixtures in `tests/fixtures/`.

---

## Acceptance Criteria
- Running CLI prints a ranked table even if some venues fail (partial success).
- SQLite accumulates funding snapshots across runs and improves 24h average accuracy over time.
- Streamlit app runs locally and on Streamlit Cloud; refresh works.
- Clear logging and `notes` column explain missing data.

---

## Start Now
1) Scaffold the repo + pyproject dependencies (python 3.11+).
2) Implement core ingestion + sqlite + ranking using just:
   - Loris funding
   - Hyperliquid best bid
   - Dexscreener spot
3) Then add KuCoin, Aster, edgeX connectors.
4) Then Streamlit UI.

When unsure about an endpoint, consult the official docs and cite them in comments.
````

Notes on the key API bits this prompt relies on (so Claude has anchors):

* Loris endpoint + attribution requirement: ([Loris Tools][1])
* Dexscreener token endpoints (tokens/pairs/search/token-pairs): ([DEX Screener Docs][2])
* Hyperliquid `info` + `l2Book` pattern: ([Chainstack][3])
* KuCoin futures “current funding rate” + orderbook docs exist (so it should look up the best-bid endpoint): ([KuCoin][4])
* edgeX API domains (HTTP + WS): ([edgeX Docs][5])
* Aster API docs repo pointer: ([Aster.dex][6])


[1]: https://loris.tools/api-docs "Loris Tools - Crypto Funding Rate Arbitrage Screener"
[2]: https://docs.dexscreener.com/api/reference "Reference | DEX Screener - Docs"
[3]: https://docs.chainstack.com/reference/hyperliquid-info-l2-book?utm_source=chatgpt.com "l2Book | Hyperliquid info"
[4]: https://www.kucoin.com/docs-new/rest/ua/get-current-funding-rate?utm_source=chatgpt.com "Get Current Funding Rate - KUCOIN API"
[5]: https://edgex-1.gitbook.io/edgeX-documentation/api?utm_source=chatgpt.com "API Docs - edgeX Docs - GitBook"
[6]: https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation?utm_source=chatgpt.com "API documentation - Aster Docs"
