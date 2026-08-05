# Phase 12 Overview

Phase 12 adds the durable portfolio and operations foundation for Bensim Trading.

Implemented scope:

- Paper-only portfolio records under `phase12_portfolios`.
- Approved strategy membership records under `phase12_portfolio_memberships`.
- Versioned strategy allocations under `phase12_strategy_allocations`.
- Immutable snapshot records under `phase12_snapshots`.
- Deterministic accounting, valuation, performance, attribution, risk, execution analytics, journal draft, operations, replay, and report service modules.
- Read-only replay controls.
- Operations Center and portfolio operations frontend routes.

Safety boundary:

- No live trading is enabled.
- No broker submission behavior is changed.
- AI and Research Agent remain recommendation/explanation only.
- Allocation and strategy changes require explicit API mutation and authorization.

Known gate:

- Real IBKR TWS or IB Gateway acceptance remains external operational verification before any live-readiness phase.
# Phase 12.1 Update

Phase 12.1 adds durable persistence for report schedules, replay sessions, journal entries, journal notes, attribution records, risk snapshots, alert rules, and incident timeline entries. See `docs/PHASE12_1_COMPLETION_AUDIT.md` for the current completion gate status.
