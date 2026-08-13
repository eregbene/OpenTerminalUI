"""Historical Market Intelligence.

Gives the MT5 Autonomous Entry Engine and the Adaptive Trade Manager access to trustworthy
historical evidence about similar market/trade patterns, built from Bensim's OWN production
strategy logic (backend/mt5_strategies) replayed point-in-time over real historical data --
never a simplified/duplicated backtest reimplementation.

Architecture (see each module's docstring for detail):
    historical ingestion/data quality (providers/, ingestion.py, quality.py)
    -> production-strategy replay (replay.py), verified for point-in-time parity (parity.py)
    -> fingerprints/outcomes -> statistical intelligence -> Redis fast cache   [later phases]
    -> SHADOW validation -> DEMO_ACTIVE entry intelligence -> DEMO_ACTIVE adaptive intelligence

Postgres remains authoritative; Redis is a fast lookup cache in front of it, never a second
source of truth. All four MT5 accounts share the same market/pattern intelligence (a market
observation like "GBPUSD breakout + momentum" is not account-specific); risk sizing, FTMO
limits, portfolio protection, and execution remain entirely account-specific and untouched by
this package.

Three explicit modes (modes.py): OFF -> SHADOW -> DEMO_ACTIVE. Defaults to OFF. Nothing in this
package calls into the live entry gate or the Adaptive Trade Manager's mutation path in Phases
1-2 -- this is pure ingestion/replay/verification work, with zero behavioral effect on live or
DEMO trading regardless of configured mode.
"""
