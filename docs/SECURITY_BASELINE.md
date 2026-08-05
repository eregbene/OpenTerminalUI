# Security Baseline

## Fixed In Phase 3

- `BENSIM_ENV` is honored by runtime secret validation.
- Request and correlation IDs are emitted for API responses.
- Standard custom API errors avoid stack traces and support stable error codes.

## Open Issues

- WebSocket authentication remains inconsistent across channels.
- CORS should be narrowed for production deployments.
- Bootstrap admin behavior should remain documented and monitored.
- Some debug/development conveniences remain available in local mode.

## npm Audit

The Docker build currently reports 14 npm vulnerabilities: 1 low, 6 moderate, 6 high, 1 critical. Phase 3 did not run broad dependency upgrades. A separate dependency-modernization phase should classify reachability and upgrade direct dependencies first.
# AI Evidence API Hardening

The AI evidence and explanation APIs require authenticated users, ignore caller-controlled identity fields, enforce entity/bundle scope, redact sensitive values, rate-limit requests, bound responses and persist evidence/audit records under a fixed canonical root with atomic writes.

No AI endpoint has order execution, broker, shell, workflow mutation or unrestricted HTTP authority.
## Phase 10 AI Baseline

The production AI hardening layer adds the following controls:

- Managed secret lookup through `backend/ai_secrets`, with environment-backed and development encrypted-file stores.
- Provider circuit breaker state, retry accounting, timeout metadata, cancellation flags, and fallback reporting.
- Budget reservation and reconciliation before and after provider calls.
- Usage ledger records for token counts, cost estimates, pricing version, provider, model, user, conversation, and optional research job.
- Research Agent policy gates, explicit human approvals, kill switches, tenant checks, audit records, and forbidden-action detection.
- No autonomous path is allowed to create orders, approve risk, promote candidates, activate deployments, mutate accounts, run shell commands, or alter source/schema.
## Phase 11 Broker Baseline

Broker operations are paper-only. Live trading is disabled in code. Broker credentials and sensitive connection settings are not returned by APIs. Broker mutations require canonical OMS orders, deterministic risk approval, paper account verification, account allow-listing, idempotency and user confirmation.
# Phase 12 Portfolio Safety

Phase 12 remains paper-only. AI and Research Agent components do not receive authority to mutate portfolios, allocations, strategies, risk, OMS, broker state, or live trading. See `docs/PHASE12_SECURITY.md`.
# Phase 12.1 Security Note

Portfolio operations remain paper-only. AI and Research Agent capabilities remain read-only for portfolio workflows and cannot mutate allocations, risk limits, alerts, incidents, orders, or positions.

# Phase FX-3 Security Note

Forex frameworks are analytical and read-only. They reuse deterministic forex intelligence and market-structure evidence, persist signals, and return confluence summaries, but they do not create orders, approve risk, change strategies, mutate accounts, call brokers, or perform unrestricted external access.

# Phase FX-4 Security Note

Forex strategy execution remains paper-only. Frameworks cannot create orders, AI cannot approve or submit orders, and XAU/USD is blocked for execution while it is sourced from a futures proxy. Candidate approval maps into the existing paper OMS and simulator path after deterministic risk evaluation.

# Phase FX-5 Security Note

IBKR paper execution requires account verification, paper environment checks, contract verification, reconciliation gates, and explicit execution provider selection. `IBKR_PAPER` does not fall back to the simulator. Live accounts, ambiguous accounts, XAU/USD, recovery-in-progress, and duplicate order references are blocked.
# FX-5B Security

`IBKR_MODE=LIVE` is unsupported. `IBKR_MODE=PAPER` rejects fixture adapters, live accounts, ambiguous accounts, fixture contract IDs, unknown submissions, and XAU/USD execution.
