# OpenTerminalUI (Bensim Trading): repo summary for Claude chat

Paste this at the start of a Claude chat to give it context. Accurate as of 2026-09-24.

## What it is
An open-source financial terminal (FastAPI backend + React/Vite/TypeScript frontend) whose main
live use is an **autonomous MT5 forex trading system** on a demo account. The strategies come from a
mentor's SMC/ICT video course ("Faiz"), and the current version is called **BSI V3**.

## Stack and runtime
- **Backend:** Python 3.11 and FastAPI in Docker (`openterminalui-backend-1`), with Postgres and Redis containers.
- **Frontend:** React 18, Vite 6 and TypeScript, in `frontend/`.
- **MT5 link:** the backend can't run MetaTrader in Linux, so it calls a **Python bridge**
  (`python -m backend.brokers.mt5.bridge`, a Windows process) over HTTP at
  `host.docker.internal:<port>`. The bridge drives `terminal64.exe`. Runbook:
  `docs/mt5-unavailable-runbook/README.md`.
- **Account:** only `ftmo_demo_100k` is enabled (ICMarketsSC-Demo, about $98k). The bridge is on port 8773.
- **Config:** `.env` holds the values and `docker-compose.yml` lists which variables reach the
  container. A variable set only in `.env` does nothing inside the container unless compose lists it.

## Key backend folders
| Path | Role |
| --- | --- |
| `backend/brokers/mt5/` | Adapter, client, bridge, `autonomous.py` (trading scheduler, V3 fast watcher), `execution.py` (sizing and order send), `prop_risk.py`/`prop_state.py` (drawdown limits), `outcome_resolver.py` |
| `backend/mt5_strategies/` | Strategy context (`context.py`), families, `fusion.py` (candidate building), `models.py` (activation switches) |
| `backend/mt5_strategies/families/bsi_v3_runtime_detectors.py` | **The 26 BSI V3 strategies**: `RUNTIME_SPECS` (source videos, timeframes, symbols, management model) plus the detectors and the planned-entry queue |
| `backend/adaptive_management/` | Adaptive trade manager (`service.py`); `v3_faiz.py` = V3 per-strategy break-even and partial rules |
| `backend/portfolio_execution/` | Portfolio and correlation risk gates |
| `backend/historical_intelligence/` | Replay and backtest engine, execution costs |
| `docs/bsi_updated_faiz/` | Course reconstruction: rulebook, specs, validation reports |
| `data/faiz_updated_course/transcripts/raw/` | Whisper transcripts of all 125 course videos (source of truth for the rules) |
| `scripts/` | Bridge start (`start-mt5-bridges.ps1`), V3 profile scheduler, validation scripts |

## How a trade is taken (V3)
1. **Every M5 candle**, the multi-account scheduler builds a strategy context per symbol and the
   26 V3 detectors create **planned entries**: a point of interest, an entry, a stop at the recent
   swing, and a **fixed-R target** (3R for the 9:30 model, 2R for the others). These go into a
   pending queue.
2. While `BSI_V3_PLANNED_ENTRY_LIVE=true`, the M5 cycle itself doesn't place orders. It logs
   `BSI_V3_PLANNED_ENTRY_ONLY`, and all non-V3 strategies are blocked.
3. **The fast watcher (every 10 s)** checks when price touches a plan's point of interest, waits for
   M1/M5 confirmation, then re-anchors the target to the live price, keeping the strategy's R.
4. Gates before sending an order:
   - Plan confidence ≥ 65 and execution confidence ≥ 70.
   - Confluence required: more than one V3 strategy agreeing on the same thesis.
   - At most 5 accepted entries per account per day, and a 60-minute cooldown per symbol.
   - Risk:reward ≥ 1.25 and stop ≥ 15 points.
   - The per-strategy NY session windows inside the detectors.
5. **Sizing and risk** (`execution.py`, `prop_*.py`, portfolio gates):
   - Risk per trade: 0.25% of equity, capped at $250. Maximum 2.00 lots.
   - Total open risk ≤ 1.5% ($1,000). Correlated risk ≤ 1.125%.
   - Daily loss blocks new entries at 2% or $1,500. Total drawdown blocks at 5%.
   - Positions: max 10 open, 1 per symbol, 3 per currency, no opposing positions on one symbol.
   - Trades: max 20 per day, 3 per symbol per day. 15-minute cooldown after a trade; stop after 5 losses in a row.
   - Spread must be ≤ 20% of the stop distance.
6. The order is sent through the bridge. The SL and TP are placed at the broker.

**Symbols:** EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD.
The index-only strategies (9:30 model, ICT Silver Bullet) never fire, because no indices are enabled.

## Adaptive manager (open positions)
- It runs every 10 s in `demo_active` mode.
- V3 positions are recognised by their MT5 comment code (`v3flow`, `v3react+v3spec`, ...), which is
  converted to the canonical `bsi_v3_*` ID. Each strategy's management model comes from `RUNTIME_SPECS`.
- The rules below were checked against the video transcripts:
  - **Hold to target:** Order Flow, SMT, 9:30, Reactionary.
  - **Break-even at 1R:** 4H OB, Silver Bullet, Yin Yang, Monday Range, Session SMT.
  - **50% partial + break-even at 1R:** IFVG, 4H Candle Ranges.
  - **Partial + break-even at the 0.5 level of the range:** 1H CRD, Enigma, Turtle Soup. Approximated
    as halfway from entry to target, because ranges are not stored.
  - **Partial at 2R + break-even at 1R:** ABC, ABCD, MMXM, Holy Grail and other liquidity-target models.
- Settings: `BSI_V3_ADAPTIVE_*` in `.env` (break-even 1.0R, partial 2.0R at 70%, range partial 50%).

## Conventions and rules
- **Deploy:** `docker compose build backend`, then `docker compose up -d backend`, check the logs,
  and relaunch the historical watchdog (`docker exec -d ... watchdog_historical_workers.py`).
  `docker cp` alone is lost on the next container recreate.
- Tests: `python -m pytest backend/tests/...`. Some tests fail on a dev machine because they read the
  live `.env` (for example `BSI_V3_PLANNED_ENTRY_LIVE=true`); these are known and not regressions.
- Every new env threshold must also be added to `docker-compose.yml`.
- Live demo trading has priority over research CPU. Prefer evidence and forward validation over tuning.

## Known open issues
- The V3 confluence count is inflated: most detectors share one fair value gap finder and differ
  mostly by time window, so one setup can count as several strategies.
- Several detectors approximate the videos (Asian V2 doesn't measure the Asian range; Holy Grail
  checks the H1 trend instead of a sweep of the previous day's high or low; range models don't track
  the range).
- Replay results for the planned-entry model (about 70% win rate, profit factor 2.5) are unproven
  until live results confirm them.
