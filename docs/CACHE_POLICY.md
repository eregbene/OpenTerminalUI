# Cache Policy

Implemented in `backend/market_data/cache_policy.py`.

Policies exist for:

- quotes
- order books
- intraday bars
- daily bars
- instrument metadata
- fundamentals
- economic data
- news
- options chains
- market calendars

Cache keys use Bensim `market_data:*` conventions under the shared Redis prefix. Cached data is never labelled realtime by the canonical provenance model.
