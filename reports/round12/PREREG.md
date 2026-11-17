# Round 12 pre-registration (written 2026-11-07 BEFORE running): monthly currency factors -- carry, momentum, value, combo

Why: literature (Levich & Poti; Asness-Moskowitz-Pedersen "Value and Momentum Everywhere"; Menkhoff et al. "Currency
Value"; Barroso & Santa-Clara "Beyond the Carry Trade") finds monthly FX predictability survives costs while daily/intraday
does not. Here: carry alone (weak, round 6) and time-series momentum (CTA) were tested before; cross-sectional VALUE and the
COMBINATION never were.
Data (all BIS, monthly, end of period): USD exchange rates (WS_XRU), policy rates (WS_CBPOL), real effective exchange
rates broad (WS_EER). Universe USD EUR JPY GBP CAD CHF AUD NZD. Sample 1999-02 .. 2026-10 (EUR from 1999).
Signals at the end of month t (only data public by then):
- CARRY = policy rate at end of t.  - MOM = spot return vs USD over months t-2..t (3 months).
- VALUE = -(log REER[t-1] - log REER[t-61]) (5-year real change, REER lagged one month for publication delay).
- COMBO = mean of the three cross-sectional z-scores.
Portfolio: rank the 8 currencies, long top 3 / short bottom 3, equal weights, hold month t+1. Return = spot leg returns +
policy-rate differential /12 - costs: 3 bp per traded leg at rebalance (turnover-based) - swap markup 0.75 %/yr per unit side.
Periods: P1 1999-2007, P2 2008-2016, P3 2017-2026 (P3 = mostly after the factor papers were published).
PASS (each of CARRY, MOM, VALUE, COMBO): mean monthly net > 0 in P1, P2 and P3; monthly t >= 2 over the whole sample;
Benjamini-Hochberg q 0.05 over the 4. Reported only: markup 0 / 1.5 %/yr, $ on the 100k account at 1 / 5 / 7 lots per side.
