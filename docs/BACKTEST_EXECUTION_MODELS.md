# Backtest Execution Models

Default execution model:

- order type: `market_on_next_bar`
- same-bar fill: disabled
- fill policy: `conservative`
- entry delay: one bar

Supported policy names:

- `conservative`
- `optimistic`
- `stop_first`
- `target_first`
- `no_fill_on_ambiguity`
- `lower_timeframe_required`
