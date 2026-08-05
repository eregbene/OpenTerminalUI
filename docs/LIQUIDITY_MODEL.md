# Liquidity Model

Implemented references:

- buy-side liquidity above confirmed swing highs
- sell-side liquidity below confirmed swing lows

Equal high/equal low clustering is represented in configuration tolerance but only the swing-derived level model is implemented in the first vertical slice.

Sweeps require breach plus reclaim. A breakout that closes beyond the level is not classified as a sweep.

The model does not infer actual resting orders.
