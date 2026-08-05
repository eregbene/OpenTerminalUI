# Roadmap

This roadmap is limited to preparation work before Bensim-specific functionality. It does not propose new trading features.

## Phase 0 Completion Criteria

- Repository architecture documented.
- Existing build and runtime verified.
- Test blockers identified.
- Current modules classified.
- Technical debt recorded.

## Phase 3 Core Hardening

- Bensim configuration aliases added while retaining legacy variables.
- Docker identity supports Bensim image/project naming while preserving old volumes.
- Shared contracts introduced for API errors, market-data status, events, jobs, WebSockets, and service interfaces.
- Liveness/readiness endpoints and request/correlation IDs added.
- Unified verification scripts added.

## Recommended Order Before New Features

1. Stabilize the remaining full Playwright suite failures.
2. Complete provider-by-provider migration to the market-data status contract.
3. Migrate representative jobs to persisted `JobRecord` semantics.
4. Add authenticated operational diagnostics for detailed health and Redis state.
5. Classify npm audit reachability and plan dependency upgrades without force updates.
6. Prepare real market-data provider architecture and data-quality controls before strategy implementation.

## Future Module Fit

- Research Lab: current research and research-autopilot modules.
- Optimization Lab: portfolio optimizer and riskfolio modules.
- Validation Lab: model lab, robustness, walk-forward, stress tests.
- Strategy Engine: strategy runner and algorithm framework.
- Risk Engine: existing risk engine, factor attribution, scenario/stress services.
- Broker Layer: new abstraction beside, not inside, existing Kite-specific code.
- AI Analysis: existing AI/agent/LLM services.
- Trading Engine: paper trading, OMS, execution simulation, and guardrails.
# Phase 4 Completed Foundation

Market data now has canonical models, provider registry metadata, deterministic routing, validation, resampling, calendar wrappers, cache policies, snapshot metadata, diagnostics and representative frontend quality indicators. The next phase is Phase 5 -- Deterministic Market Structure and Smart Money Concepts Engine.
# Phase 5 Status

Deterministic market structure and SMC vertical slice is implemented for research, overlays and future strategy inputs. It does not generate trades.

Next recommended phase: Phase 6 - Deterministic Strategy Framework and Rule Execution.
# Phase 6 Complete: Deterministic Strategy Framework

The foundation now includes validated strategy specs, deterministic rule evaluation, reference strategies, proposal-only trade intents, API endpoints, a chart inspector, and focused tests.

Next recommended phase: Phase 7 — Research, Backtesting, Optimization and Validation Integration.

# Phase 7 Complete: Research Validation Workflow

The canonical workflow now supports strategy backtests, metrics, optimization, walk-forward validation, robustness, scorecards, candidates, lineage, artifacts, API and a Research Lab UI.

Next recommended phase: Phase 8 — Risk Engine, Paper Trading and Order Management Integration.

# Phase 9C Complete: Persistent AI Evidence Retrieval

The AI assistant now has read-only adapters for real domain stores, canonical entity references, persisted evidence bundles, lineage retrieval, and grounded deterministic explanation responses.

Next recommended phase: LLM/provider integration planning, still behind the read-only evidence and authorization boundary.
# Phase 9C.1 Complete: AI Evidence Security Hardening

AI evidence APIs are now authenticated, entity-scoped, redacted, bounded, auditable and protected against common file-backed persistence and API abuse cases.

Next recommended phase: Phase 9D - LLM Provider Abstraction and Grounded Summarization.

# Phase 9D Complete: Grounded AI Presentation Layer

The AI assistant now has a provider abstraction, OpenAI-compatible adapter, deterministic fallback, prompt builder, citation handling, output validation, token budgeting, SSE chat endpoint, owner-scoped conversation persistence and Agent Console evidence display.

Next recommended phase: Phase 10 - product hardening and operational readiness for the Bensim workstation.

# Phase 10 Complete: Production AI Research Controls

Provider hardening and the bounded Research Agent establish the production control plane for research-only autonomous intelligence. The next phase should focus on replacing file-backed stores with durable multi-node services and deepening real research workflow adapters.
## Phase 11

Phase 11 introduces IBKR paper broker integration behind a canonical broker abstraction. Live trading remains disabled. Future work should replace the deterministic simulated adapter with manually verified TWS/Gateway paper connectivity before production use.
# Phase 12 Note

Portfolio operations, durable snapshots, deterministic accounting/risk/execution analytics, reports, and Operations Center foundations are now represented in Phase 12. Real IBKR acceptance remains an external gate before any live-readiness phase.
# Phase 12.1 Roadmap Note

Do not begin Phase 13 until the remaining Phase 12.1 gates are closed: full backend suite, frontend suite, Playwright workflows, scheduled report execution, full operations health probes, and performance/soak tests.

# Phase FX-3 Roadmap Note

Forex-first work now includes `XAUUSD` as a labelled gold futures proxy and a normalized read-only framework layer for deterministic confluence. Before expanding to execution, replace the XAU/USD proxy with a true spot/broker source and graduate limited-data frameworks only after deterministic evidence and validation tests exist.

# Phase FX-5 Roadmap Note

IBKR paper acceptance scaffolding is present with mocked verification, contract, order ledger, reconciliation, and recovery tests. The next operational gate is a real TWS/Gateway read-only acceptance followed by exactly one manually approved EUR/USD paper trade.
# FX-5B Status

PostgreSQL persistence and real-mode safety gates are in place. The remaining external milestone is verified TWS/Gateway paper connectivity and one manually confirmed EUR/USD paper acceptance trade.
