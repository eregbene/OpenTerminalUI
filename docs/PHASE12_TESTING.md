# Phase 12 Testing

Focused tests added:

- Portfolio accounting and allocation invariants.
- Portfolio risk concentration, correlation, VaR/CVaR, and limits.
- Execution analytics slippage, latency, partial fill, and anomaly handling.

Full Docker and Playwright gates remain required for complete Phase 12 acceptance.
# Phase 12.1 Testing Update

Focused persistence hardening tests live in `backend/tests/test_phase12_1_persistence_hardening.py` and cover restart persistence, idempotent ledger ingestion, allocation activation history, tenant isolation, replay read-only controls, and journal idempotency.
