# Data sources

The external data budget is **$0**, and only public market data is used. No trading keys are used anywhere.

Each row has one of three statuses:
- **Verified**: confirmed on the provider's official page or by a live call on the date shown.
- **Unverified**: not confirmed. Treat it as unknown.
- **Blocked**: the page could not be read by automated fetch. The user should read it in a browser.

Checked 2026-10-30.

## Provider matrix

| Provider | What it gives | History | Limits | Licence / terms | Status |
|---|---|---|---|---|---|
| **Coinbase Exchange** public API (`api.exchange.coinbase.com`; CCXT `coinbaseexchange`) | OHLCV candles; product list with `status` | BTC-USD daily from **2015-07-20** (live call). Docs: "Historical rate data may be incomplete." | 300 candles/request. Granularities 60, 300, 900, 3600, 21600, 86400 s (**no 4h**). 10 req/s per IP, bursts to 15; 429 when exceeded. | Developer ToS: **blocked** | Limits verified ([candles](https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles), [rate limits](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)). ToS unverified. |
| Coinbase `/products` | All products, including **delisted** ones (live: 845 total, 517 `online`, 328 `delisted` with `trading_disabled=true`) | n/a | as above | as above | Verified live; not documented. Candles are still served for delisted products (live: FX, UNFI and REP each returned 300 days), which matters for survivorship-free altcoin history (v0.6). |
| **Bitstamp** public API (`/api/v2/ohlc/{pair}/`; CCXT `bitstamp`) | OHLCV | btcusd from **2011-08-18** (live call) | 1–1000 candles/request. Steps include 14400 (4h) and 86400. 400 req/s, 10,000 per 10 min. | Terms page: **blocked** (empty) | Limits verified ([api](https://www.bitstamp.net/api/)). Terms unverified. |
| **Coin Metrics Community** API | `PriceUSD` daily (close only, no OHLC) | BTC from **2010-07-18** (live call). `ReferenceRateUSD` is only the last ~7 days on community. | 10 requests per rolling 6 s per IP; no key | **CC BY-NC 4.0**: non-commercial, attribution required | Verified. Personal research complies; credit "Coin Metrics" in reports that use it. |
| Kraken REST OHLC | OHLCV | Only the most recent 720 candles, so no backfill | — | Not read | Verified ([docs](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data)) |
| Kraken OHLCVT archive | Bulk OHLCVT zips, every pair from its first trade up to 2026-06-30, with quarterly updates | Full | — | **No licence stated** | Verified ([article](https://support.kraken.com/articles/360047124832)). Don't use until the terms are clear. |
| data.binance.vision | Bulk klines | Deep | — | CC BY-NC-SA 4.0; personal backtesting allowed. Governing law defers to the Binance ToS (not read). | **Not used.** The user is in the US, and eligibility under the Binance ToS is unverified. |
| CoinGecko Demo | Current market data | 1 year only | 100 calls/min, 10k/month; attribution required | — | Verified ([pricing](https://www.coingecko.com/en/api/pricing)). **Global market cap and dominance history are paid only**, so dominance history is **unavailable**. |
| DefiLlama (`api.llama.fi`) | TVL, fees, revenue | Varies | Not stated | Not stated | Free and keyless verified ([docs](https://api-docs.defillama.com/llms-free.txt)). Planned for v0.7. |

## Observed behaviour (live runs, 2026-10-30)

- Coinbase and Bitstamp both return the **in-progress UTC day** as a candle. Collectors store only completed days.
- Both return candles oldest first, and nothing for windows before a market's first trade. That is how the backfill finds where history starts.
- Coin Metrics `PriceUSD` for day D matches the close of the exchange candle starting on D. The median absolute difference is 0.10% for Coinbase and 0.14% for Bitstamp, against ~1.4% when shifted a day either way.
- At 3% tolerance, Bitstamp BTC/USD shows 154 reference mismatches. They are concentrated in 2011–2012 (136), an illiquid early market where exchange prices really did diverge. Coinbase BTC/USD shows 2.
- The API pages from the newest end by default, so the collector requests `paging_from=start`.
- Coinbase ETH/USD has a 3-day gap in May 2016, just after listing.

## Libraries

| Library | Status (2026-10-30) | Use |
|---|---|---|
| CCXT 4.5.91 | MIT, Python ≥3.10. Public `fetch_ohlcv` for `coinbaseexchange` (300 max) and `bitstamp` (1000 max), per source; no keyless call run yet. | Exchange collectors |
| TA-Lib 0.8.3 | PyPI wheels for Windows, cp311–cp315 | Candidate reference implementation for indicator tests (v0.2) |
| pandas-ta | GitHub repo 404s; last PyPI release is a 2025 beta needing Python ≥3.12 | **Don't use** |

## What we use

- **v0.1:**
  - **Coinbase Exchange** daily candles for every USD market, from listing. This is the primary venue.
  - **Bitstamp** btcusd daily from 2011-08-18 for long-cycle BTC history.
  - **Coin Metrics `PriceUSD`** as an independent reconciliation check on BTC closes.
- **BTC dominance:** recorded from today forward once a free current-value source is chosen. Its history is **unavailable**.
- **Fees:** the Coinbase Advanced fee page is blocked to automated fetch, and third-party figures conflict (0.40/0.60, 0.50/0.90 and 0.35 maker). Fees are a cost-profile setting the user confirms, never a constant in code.

## Open items for the user

1. Read the [Coinbase Developer Platform terms](https://www.coinbase.com/legal/developer-platform/terms-of-service) and [Bitstamp terms](https://www.bitstamp.net/terms-of-use/), and confirm that using public market data for personal research is fine.
2. Confirm your current Coinbase Advanced maker/taker fee tier.
