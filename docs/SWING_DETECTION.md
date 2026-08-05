# Swing Detection

Implemented method: `fractal`.

Configuration:

- `left_bars`
- `right_bars`
- `minimum_separation_bars`
- `minimum_price_movement`
- `minimum_atr`

Confirmation timing is point-in-time safe. A pivot at bar `N` is not confirmed until bar `N + right_bars` closes. The model stores both `candidate_time` and `confirmation_time`.

Incomplete bars are ignored by default through `input.use_completed_bars_only=true`.
