# Round 31 pre-registration (written 2026-11-11, BEFORE running anything on FXCM)
Data: FXCM M5 bid bars + real spread, 25 pairs (8 currencies, no gold), 2012-01 .. 2026-10.
Trade = the user's first table, exactly as round 28 defines it: FVG on the TF (gap >= 0.3 ATR14), liquidity = lowest low of
bars i-6..i-2, enter at the zone top on the first touch (within 100 TF bars), stop = liquidity - 5 pips (0.05 JPY pairs),
target 1:3, max 100 TF bars, stop first on the same bar, cost = the pair's median FXCM spread + 0.2 pip, in R.
Hypotheses (judged ONLY on 2012-01 .. 2021-09, never used for this idea; 2021-10+ shown for information):
  H1  1D zone in Fibonacci DISCOUNT (zone top in the lower half of the last 50 daily bars' range; premium for sells)
  H2  1H zone with BOS + liquidity > 2 ATR away + discount + touch 07-17 UTC
Pass = total R > 0 and t >= 2 on 2012-01 .. 2021-09. The base first table (1H / 4H / 1D, no filters) is reported for reference.
