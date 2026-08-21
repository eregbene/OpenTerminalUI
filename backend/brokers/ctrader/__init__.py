"""cTrader Open API broker adapter (Phase 2 of the broker-independence migration).

READ-ONLY in this phase: connect/authenticate/account-discovery/positions/symbols/quotes/
historical-bars only. No order submission, modification, cancellation, or position closing --
see adapter.py::CTraderAdapter for the explicit CTraderReadOnlyViolation guards mirroring
brokers/mt5/adapter.py's own MT5ReadOnlyViolation posture from Phase 1.

Never imports credentials from anywhere but environment/config -- see config.py.
"""
