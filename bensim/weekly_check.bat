@echo off
rem FRIDAY FORWARD CHECK: live fib618 vs base vs the frozen CANDIDATE (liquidity >=1R ahead + 2R target) on the
rem finished week (run it Friday after 21:00 UTC or at the weekend). Replays from the DB, places no orders.
rem Running total: data\forward_check.csv -- judge at ~50 candidate trades. Works without Claude.
cd /d "%~dp0.."
set PYTHONPATH=%CD%
rem refresh the last ~3 weeks of candles first (the replay reads the DB; stale candles = missing trades)
python -m bensim.history --years 0.06 --symbols EURUSD,GBPUSD,USDCHF,USDCAD,XAUUSD,AUDUSD,USDJPY
python -m bensim.weekly_check
pause
