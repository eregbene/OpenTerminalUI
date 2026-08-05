# Data Source Inventory

Phase 4 inventory is based on traced imports, route dependencies, clients and existing tests, not filenames alone.

| Source | Asset classes / data | Auth | Behavior | Cache / fallback | Known consumers | Migration status |
| --- | --- | --- | --- | --- | --- | --- |
| Yahoo Finance / yfinance / chart API | equities, ETFs, indices, crypto; quotes, chart candles, fundamentals summary | none configured | delayed/polled; no realtime entitlement claimed | used as fallback in adapters and historical service; synthetic fallback exists in some historical paths | charts, alpha/factors, backtests, crypto adapter, Yahoo adapter | registered; normalized quote/OHLCV adapter helpers added; many endpoints still legacy |
| NSE public website | Indian equities, indices, FNO; quotes, market status, corporate info, chart endpoint | cookie/session only | can return 403/429; existing circuit breaker opens on repeated blocks | callers fall back to Yahoo/mock where implemented | quotes, market status, depth/FNO, results calendar | registered; legacy client remains |
| Kite / Zerodha | Indian market quote/stream capabilities | API credentials/access token | authenticated; permission failures possible | legacy adapter falls back to NSE/Yahoo | Kite routes, stream service | inventoried; not integrated with new broker/execution |
| Alpaca | US equities quotes in adapter path | credentials if configured | legacy adapter support | fallback through adapter registry | adapter registry | inventoried; not migrated |
| FMP | quotes, fundamentals, news, US options chain | API key | plan/endpoint entitlements vary | callers fallback on 401/402/errors | fundamentals, news, US options | registered; key redacted |
| Finnhub | quote/news client | API key | provider limit/error handling in client | callers fallback where implemented | news/quotes | registered; key redacted |
| FRED | economic series | API key | economic data only | legacy route behavior | economics terminal | registered |
| CoinGecko / crypto service | crypto spot/markets | no key in current path | polled/delayed | Yahoo fallback in crypto adapter | crypto pages | inventoried; not fully migrated |
| Binance WebSocket | crypto streaming test/service paths | public stream | streaming where connected | reconnect logic in legacy service/tests | crypto WebSocket services | inventoried; not fully migrated |
| Internal demo/mock/synthetic | all representative types | none | simulated/demo/fallback only | explicit fallback source | tests, demo UI, historical fallback | registered as `internal-demo`; never realtime |
| Redis quote bus / marketdata hub | internal quote/depth/candle relay | internal | WebSocket relay/aggregation | Redis and in-memory layers | frontend streams, paper engine, alerts | inventoried; canonical event integration remains partial |
| Local SQLite/Postgres | users, alerts, watchlists, cached app data, OHLCV cache tables | app config | persistent local storage | existing DB cache | many routes | storage abstraction documented; no large migration |

Known generated timestamps: demo/synthetic rows and some route fallbacks use application-generated timestamps. Provider timestamps are preserved in the new canonical models when available.
