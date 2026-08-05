# Risk Assumptions Audit

| Behavior | Current Status | Phase 8 Treatment |
|---|---|---|
| Position sizing | duplicated/approximate | Canonical sizing supports fixed units, fixed notional, percent equity, percent risk, simplified volatility/equal/portfolio weight. |
| Max position size | partial | Enforced by `instrument.maximum_notional` and position policy caps. |
| Leverage | approximate | Buying power is simulated; broker-equivalent margin is not claimed. |
| Margin | absent | Orders are blocked by simulated buying power only. |
| Short selling | unsafe/partial legacy | Canonical policy defaults short selling off unless position reduction is detected. |
| Concentration | partial | Gross exposure, net exposure and instrument notional are enforced; sector/correlation only documented when metadata exists. |
| Daily loss | partial | Policy fields exist; full session P&L enforcement remains a limitation. |
| Drawdown | partial | Account high-water mark tracked; hard actions are documented for future expansion. |
| Stop requirements | implemented | Invalidation is required by default for new canonical intents. |
| Stale data | implemented | Blocked unless policy allows stale data. |
| Duplicate orders | implemented | Deployment/proposal/idempotency key prevents duplicate paper orders. |
| Order frequency | partial | Policy field exists; hourly counter persistence is not complete. |
| Account currency | implemented foundation | Base/quote currency is explicit. |
| Currency conversion | implemented foundation | Non-base instruments require an explicit conversion rate. |
| Portfolio limits | partial | Exposure, pending orders and buying power are enforced. |
| Emergency disable | implemented | Overrides new canonical strategy activity. |
