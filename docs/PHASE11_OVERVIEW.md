# Phase 11 Overview

Phase 11 adds a paper-only broker layer for Interactive Brokers workflows. Live trading remains disabled.

The implementation order is:

1. Stabilize renderer performance.
2. Preserve canonical OMS and risk flow.
3. Add broker abstraction.
4. Add IBKR paper adapter and operations UI.
5. Verify safety, account isolation, contract resolution, market-data labeling, and OMS-only submission.

Real TWS/Gateway connectivity is represented by configuration and a protocol-faithful simulated adapter in automated tests. Manual TWS/Gateway verification is still required before using a real IBKR paper session.
