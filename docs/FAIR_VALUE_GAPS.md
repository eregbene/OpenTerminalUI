# Fair Value Gaps

Implemented FVG rule:

- bullish FVG: first candle high is below third candle low
- bearish FVG: first candle low is above third candle high
- minimum gap size can be ATR-relative
- midpoint and consequent encroachment are stored
- lifecycle supports active, partial and mitigated states

Formation uses only the three bars required for detection. Mitigation updates only from later bars.

Inverse FVG and balanced price range transitions are deferred.
