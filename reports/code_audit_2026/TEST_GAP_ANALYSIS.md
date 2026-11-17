# Test gap analysis (2026-11-03)

## What the existing tests actually verify
All were run in this audit: **26 passed**, plus module self-checks for `guard._selfcheck`, `indicators._selfcheck` (incl. a causality test), `books.selfcheck` and `validate._selftest`.

| Test | Verifies | Data | Doesn't verify |
|---|---|---|---|
| `test_backtest.py` (2) | `_simulate`: long target / stop-first on a both-hit bar; short exits on ask; gap through the stop fills at the open | synthetic 2-bar | spread by time, commission, swap, Friday, time exit, add-ons |
| `test_backtest_p0.py` (1) | default Exec unchanged; Friday close never exceeded; no Friday ≥ 20:00 entries; swap applied; r = gross − commission + swap | DB GBPUSD Aug–Nov 2026 | swap values vs the broker; DST rollover window |
| `test_live.py` (3) | live path (`windows` + `plan`) triggers **every** backtest trade of one week (fib618) and of Bennett Aug–Oct; `lots_for` on 2 hand cases; guard self-check | DB bars + one stored backtest run | that live triggers **only** those trades (one-way subset check); books, flow, manager; order construction; Friday close; time exit; shadow |
| `test_timemachine.py` (4) | null model ≈ coin flip; drift removed; repo mode keeps drift; Friday close helper | synthetic | link to real trade outcomes |
| `test_parallax.py` (10) | NY-close D1 bars both DST seasons, patterns, fib legs, fills, stop-first, cancel, bid/ask | synthetic | live books two-leg behaviour (F17) |
| `test_cta_momentum.py` (4) | buy-stop fill, trail, cancel, fill-day stop | synthetic | engine-managed live trail (F16) |
| `test_flow_live.py` (2) | live features = research features at 2 timestamps | FXCM files + cache | order sizing, guard bypass (F04) |
| self-checks | guard rules on hand numbers; indicator causality (truncating the future doesn't change the state); books fill/cancel/pick; validate statistics | synthetic | — |

**No tests exist for:** `mt5_bridge`, `bridge`, `live.cycle` / `decide` / `close_all`, `books.step` / `_send`, `flow_live.step` / `_send`, `manager`, `trade_watch`, `crypto_paper`, `history` / `backfill` / `events`, `mt5_time` (beyond indirect), `strategy.plan` on hand-built cases, `fvg_strategy`, `orderflow_strategy`, `pullback_strategy`, `gf_strategy`, `sflow` live path, `tradeguard`.

## Missing tests and the recommended approach
| # | Gap | Approach | Finding |
|---|---|---|---|
| T1 | **Candle indexing / incomplete bars** | build bars where the forming M5 / M15 / H1 bar contains a trigger; assert `plan()` ignores it until closed (live `windows` and backtest `_win`) | (verified by reading; no test) |
| T2 | **Lookahead** for every strategy | for N random cut points: `plan(bars[:k])` must equal `plan` computed with extra future bars appended but windows cut at k (as `indicators._selfcheck` does) | — |
| T3 | **Price precision / pip conversion** | table test for `pip()`, digits, `cost_pips`, `_spread` for EURUSD, USDJPY, XAUUSD, crosses | F14 |
| T4 | **Lot sizing** | extend `math_checks.py` into a test: EURUSD, USDJPY, GBPJPY, USDCAD, XAUUSD, volume_step 0.01 / 0.1, `volume_min`, `volume_max` | F21 |
| T5 | **Currency conversion** | backtest USD P&L for USD-base, USD-quote, cross, metal | F14 |
| T6 | **Spread and swap** | `_spread` at the summer/winter rollover and the Sunday open; swap nights incl. Wednesday ×3 and the Friday close | F12 |
| T7 | **Stop/target same bar** | already for `_simulate`; add `strategy._exit_bar` and `books.touched` short/long cases | — |
| T8 | **Missing market data** | live `cycle` with a stale newest bar → no plan; backtest over a hole → no fills inside the hole | F07 |
| T9 | **Duplicate signals** | call `cycle` twice on the same bar (fake bridge) → one `bridge.order` | — |
| T10 | **Duplicate orders across processes** | two engines on one DB (fake bridge) → one order per signal (PK) | — |
| T11 | **Broker rejection / partial / timeout** | fake bridge returns 10006 / 10010 / raises after "executing" → recorded state is correct | F09 |
| T12 | **Position synchronisation** | fake positions mismatched with `live_signals` → shadow WARN; engine restart with open positions | — |
| T13 | **Engine restart** | Phase 3 Test 1 (run records), `initial` / `day_start` state across a restart over midnight | F11 |
| T14 | **Risk-control bypass** | every entry path (fib618, Bennett, books ×5, flow, manager) through a common fake guard: assert each is called with the same snapshot | F03, F04, F10 |
| T15 | **Friday close coverage** | fake clock Friday 20:00 → `close_all` closes every bensim magic incl. flow / books / manager; no new orders | F01, F02 |
| T16 | **Live ⇔ backtest two-way parity** | replay a week through live `cycle` (fake bridge) and `run_symbol`; assert **equal** trade sets, not subset | test_live is one-way |
| T17 | **Order construction** | buy uses ask, sell uses bid; SL/TP sides; comment / magic per book | — |
| T18 | **Data ingestion** | `history.store` overwrite detection; `backfill` insert-only; OHLC validation; DST conversion samples | F15 |
| T19 | **Daily-loss base** | `day_start_balance` when the first cycle is late | F11 |
| T20 | **Cycle duration** | fake slow bridge → cycle time recorded, alarm on > N s | F07 |

All of these run offline with a fake bridge and an isolated test database (the Phase 3 approach). None needs live order placement.
