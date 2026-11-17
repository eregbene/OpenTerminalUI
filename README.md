# bensim

Lean single-strategy FX app (donchian imbalance / fib 61.8% with LuxAlgo-style SMC confirmations).

- DB: `docker compose up -d postgres` -> database `bensim` on localhost:5432
- MT5 bridge (demo only): `powershell bensim/start_bridge.ps1` (port 8773)
- History: `python -m bensim.history --years 5`, calendar: `python -m bensim.events`
- Backtest: `python -m bensim.backtest --set key=value ...`, charts: `python -m bensim.chart --run N`

The old multi-strategy platform is on branch `archive/full-system-2026-10-09` (commit b05b5fa).
Old DB backup: `D:/Devops/trading/backups/openterminalui_2026-10-09.dump`.
