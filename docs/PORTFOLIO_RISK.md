# Portfolio Risk

Phase 12 portfolio risk is post-trade monitoring. The existing pre-trade risk engine remains authoritative for order approval.

Implemented:

- Gross/net/long/short exposure.
- Exposure by strategy, instrument, asset type, and currency.
- Concentration metrics.
- Rolling correlation primitive with minimum sample handling.
- Historical VaR/CVaR primitive with limitations.
- Percentage stress scenario.
- Deterministic limit evaluation without automatic position closing.
