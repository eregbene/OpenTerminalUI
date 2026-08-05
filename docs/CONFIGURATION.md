# Bensim Trading Configuration

## Precedence

Configuration is loaded centrally through `backend/config/settings.py`.

Precedence for application settings:

1. Explicit runtime configuration passed by code.
2. `BENSIM_*` environment variables.
3. Legacy `OPENTERMINALUI_*` environment variables.
4. Older compatibility variables where already supported, such as `OPENSCREENS_*`, `TRADE_SCREENS_*`, or provider-specific names.
5. `backend/config/settings.yaml`.
6. Documented defaults.

## Supported Bensim Aliases

Every `OPENTERMINALUI_*` setting read through `_env()` has an equivalent `BENSIM_*` alias. Examples:

- `BENSIM_APP_NAME`
- `BENSIM_APP_VERSION`
- `BENSIM_CORS_ORIGINS` (development defaults include Vite dev port `5173` and preview port `4173`)
- `BENSIM_SQLITE_URL`
- `BENSIM_REDIS_URL`
- `BENSIM_REDIS_QUOTE_CHANNELS_TTL`
- `BENSIM_REDIS_MAX_CONNECTIONS`
- `BENSIM_FMP_API_KEY`
- `BENSIM_FINNHUB_API_KEY`
- `BENSIM_AI_PROVIDER`
- `BENSIM_AGENT_PROVIDER`
- `BENSIM_AGENT_MODEL`
- `BENSIM_OPENROUTER_API_KEY`
- `BENSIM_PRICE_CACHE_TTL_SECONDS`

`BENSIM_ENV` is also supported for runtime secret validation.

## Compatibility

Legacy `OPENTERMINALUI_*` variables must not be removed until a future migration phase provides a deprecation window and release notes. Secrets are not logged by the settings loader.
