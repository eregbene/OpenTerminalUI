# Backtest Assumptions Audit

Canonical Phase 7 strategy backtest assumptions:

- Signal timing: implemented, completed-bar only.
- Order timing: implemented, proposal becomes eligible on a future bar.
- Same-bar fills: unsupported by default.
- Next-bar fills: implemented.
- Market orders: represented by market-on-next-bar.
- Limit, stop, stop-limit, limit-at-zone, stop-entry: modeled in order schema; only representative simple fills are implemented.
- Intrabar ambiguity: implemented with configurable policy; default is conservative.
- Commissions/spread/slippage: implemented in basic cost model.
- Partial fills/liquidity constraints/margin: unsupported and reported as limitations.
- Position accounting: implemented for one-symbol long/short vertical slice.
- Corporate actions/dividends/futures multipliers/forex conversion/options multipliers: modeled as lineage/asset limitations, not fully simulated.
- Missing data/delistings/rejected orders: not silently assumed; future validation scenarios flag missing bars.

Legacy backtest paths may differ and remain documented as legacy behavior.
