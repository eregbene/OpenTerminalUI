# Second-stage forensic review (2026-11-03)

**Research and documentation only.**
- No Bensim live, paper, risk, strategy or data change. No R1/R2/P3 run. Nothing committed.
- The US-stock paper runner (separate system, per the user's instruction) kept running and placed one Alpaca paper bracket order (AAPL), as authorised for that system.

**Sources** (all read-only):
- `reports/reconciliation_2026-11-03/positions.csv`, `signals.csv`, and the new `r_bridge_matched.csv`;
- DB `live_events`, `live_signals*`, `live_books`, `live_flow`, `manager_actions`;
- MT5 deals and account via the bridge's `/deals` and `/account`;
- the engine stdout log `logs/bensim_live.log`;
- the **MT5 terminal journal** `%APPDATA%\MetaQuotes\Terminal\26B571BECB9676C26C2BE0358618C190\logs\YYYYMMDD.log` (origin.txt → `E:\MT5\Bensim-100K`; journal times = **local PC time**, UTC+3 to 2026-10-25, UTC+2 after);
- `data/crypto_paper/ledger.csv`;
- source code as cited.

**Two errors in my first-stage reports, corrected here** (also logged as C7 and C8 in `docs/audit/CORRECTIONS_2026-11-03.md`):
- **C7.** The FXCM "hours lost" figures understated missing data. Holes that end on a weekend, i.e. missing whole weeks, weren't counted. Correct measure: §6.
- **C8.** The reconciliation's entry-difference column had its sign reversed: **live entries were 0.2 pip *worse* at the median**, not better.

---

## 1. Complete P&L reconciliation (2026-10-13 06:00 → run time 2026-11-03 ~14:50 UTC)
The figures in my chat summary showed only 5 of the 9 position groups. The full table (per position, from MT5 deals grouped by `position_id`, `bensim/reconcile.py::live_positions`):

| Book / group (magic) | Positions | Gross profit | Gross loss | Profit (sum) | Swap | Commission | Other costs (fee) | **Net** | Unrealized in net |
|---|---|---|---|---|---|---|---|---|---|
| fib618 (770101) | 26 (1 open) | 4,043.00 | −5,452.82 | −1,409.82 | −151.05 | 0.00 | 0.00 | **−1,560.87** | +94.58 (open GBPUSD) |
| bennett (770102) | 2 | 476.60 | −242.72 | 233.88 | −67.74 | 0.00 | 0.00 | **+166.14** | 0 |
| flow (770103) | 25 | 2,696.06 | −1,381.46 | 1,314.60 | +4.12 | 0.00 | 0.00 | **+1,318.72** | 0 |
| imbalance (770104) | 9 | 1,611.19 | −1,719.13 | −107.94 | −9.56 | 0.00 | 0.00 | **−117.50** | 0 |
| enigma (770105) | 4 | 1,233.81 | −241.53 | 992.28 | −6.95 | 0.00 | 0.00 | **+985.33** | 0 |
| sflow (770106) | 8 | 377.53 | −1,315.48 | −937.95 | 0.00 | 0.00 | 0.00 | **−937.95** | 0 |
| parallax (770107) | 0 | — | — | — | — | — | — | 0.00 | — |
| cta (770108) | 1 | 739.97 | 0.00 | 739.97 | 0.00 | 0.00 | 0.00 | **+739.97** | 0 |
| manager (770109): Claude's manual trades | 1 | 134.40 | 0.00 | 134.40 | −3.48 | 0.00 | 0.00 | **+130.92** | 0 |
| **TradeGuard test trades (magic 999001, "guard test", 0.01 lots)** | 3 | 0.00 | −0.26 | −0.26 | 0.00 | 0.00 | 0.00 | **−0.26** | 0 |
| **Total** | **79** | **11,312.56** | **−10,353.40** | **959.16** | **−234.66** | **0.00** | **0.00** | **+724.50** | **+94.58** |

- **The $179.50 the user found:** the five books in the summary sum exactly to **+$545.20**, not $545. The remainder of **+$179.30** = bennett +166.14 + imbalance −117.50 + manager +130.92 + TradeGuard test −0.26. Total +545.20 + 179.30 = **+724.50**. The 0.20 difference to the user's $179.50 comes from my rounded display figures.
- **Realized vs unrealized:** total = realized **+$629.92** (equal to the sum of profit+swap+commission+fee over every trade deal with time ≥ T0, checked independently from raw deals) plus unrealized **+$94.58** (the open fib618 GBPUSD position at run time).
- **Yes, the 79 positions include the 3 TradeGuard test trades.** They were shown as "magic 999001" in the first report's table, not excluded.

**Excluded or edge records:**
- **No balance, credit or deposit deals** in the window: deal types only 0/1 (79 buy, 78 sell deals).
- **One position opened before T0 is included** because it closed after T0: fib618 USDCAD, opened 2026-10-13 02:30 UTC, old comment format `bs 1791820800`, −$244.66. Its opening deal had $0 P&L.
- **Not included:** positions closed before T0; anything after the run time.
- **Account cross-check (same moment):** balance $98,307.11, equity $98,506.69. Absolute account values aren't comparable with the window sum because the window starts mid-history.
- **Commission is $0 and fee is $0 on every deal** (observed).

---

## 2. Complete R bridge, backtest → live

### 2.1 Definitions (different denominators)
- **Backtest R** (`backtest._simulate`, observed profile):
  - formula: (exit − entry)·dir / (entry − SL) − commission_r + swap_r;
  - entry = trigger-bar close (bid) + modelled spread + 0.1 pip slippage; exit at the modelled bid/ask;
  - denominator = **price risk at the backtest entry**.
- **Live R** (`reconcile.py`):
  - formula: (profit + swap + commission) USD / **planned risk_usd**;
  - risk_usd = loss-per-lot at the **quote seen at decision time** (~8 s after the bar) × lots, from the engine's own decision record (`live_signals.detail.risk_usd`, `live_books.extra.decision_detail`).
- **For the open position,** live R includes unrealized P&L at run time. The backtest marks it "time" at the data end (12:00 UTC).

### 2.2 Totals: every trade in exactly one category
**Backtest: 56 trades, +6.794R**

| Category | Trades | R |
|---|---|---|
| A. both systems took it | 38 | +8.719 |
| B. backtest only, live skipped it (slot / guard) | 6 | +1.547 |
| C1. backtest only, book not live yet (before 2026-10-20 10:00) | 9 | −4.575 |
| C2. backtest only, live never generated / recorded it (§3) | 3 | +1.103 |
| **Total** (8.719 + 1.547 − 4.575 + 1.103) | **56** | **+6.794** |

**Live: 41 positions in the 4 shared-code books**

| Category | Trades | R | USD |
|---|---|---|---|
| A. both systems took it | 38 | +3.244 | |
| D. live only, sent (GBPUSD short, signal 2026-10-14 13:00; path dependence: backtest still in its own trade) | 1 | −1.002 | |
| E. live, outside the backtest window: USDCAD opened before T0 (`bs …`) and USDCAD opened 2026-11-03 12:20 UTC (after the DB end) | 2 | n/a (no decision record in window) | −490.24 |
| **Total with R** | 39 | **+2.242** (positions file, full precision: +2.239) | |

### 2.3 Category A: the 38 shared trades, +8.719R → +3.244R (difference −5.475R)
Each trade gets exactly **one** cause, assigned in priority order: manual close > Friday rule > still open > same exit. So nothing is counted twice. File: `r_bridge_matched.csv`.

| Cause | Trades | Backtest R | Live R | Difference |
|---|---|---|---|---|
| closed manually (MT5 mobile/terminal; `manager_actions` "USER manual close") | 9 | +16.641 | +15.740 | **−0.901** |
| Friday rule not yet live (EURUSD long 2026-10-16: backtest Friday close +0.576; live held over the weekend, broker SL at the Sunday reopen 21:01 UTC, −$423) | 1 | +0.576 | −1.743 | **−2.319** |
| still open at run time (GBPUSD long; live includes unrealized) | 1 | −0.042 | +0.388 | **+0.430** |
| same exit type (SL↔stop, TP↔target, engine Friday↔friday_close) | 27 | −8.456 | −11.141 | **−2.685** |
| **Total** | **38** | **+8.719** | **+3.244** | **−5.475** |

The earlier −5.91R left out the +0.43R still-open row: −0.901 − 2.319 + 0.430 − 2.685 = −5.475 ✓.

**The 27 same-exit trades (−2.685R), decomposed:**

| Step | Effect |
|---|---|
| backtest R → price R on the **live fill and live exit**, with the actual fill risk | −2.615 |
| (of which) entry-price part: live paid more than the backtest's bar-close entry | −2.41 |
| (of which) exit-price part: stop/target fills vs modelled exits | −1.155 |
| (of which) backtest modelled spread / slippage / swap already in backtest R, partly offsetting | +0.95 |
| denominator: planned risk at the quote vs actual fill risk | −0.056 |
| swap actually charged | −0.018 |
| residual (USD conversion of non-USD-quote pairs, rounding) | +0.004 |
| **Total** | **−2.685** |

- **Entry prices, live minus backtest** (positive = live worse): median **+0.2 pip**, mean +1.14 pip.
  - It splits into **price movement in the ~8 s between the bar close and the quote** (median +0.2) plus **fill vs quote** (median 0.0, mean +0.11). Slippage proper is small; the timing gap is the main cost.
- **Largest single entry differences:**
  - **USDCHF 2026-11-01 22:05, +33.8 pips:** the trade filled at the **Sunday reopen**. The backtest uses the stored bar close plus its modelled spread (×4 at 20:55-22:05); the live ask at the reopen was 34 pips higher. The weekend-reopen spread isn't modelled.
  - **GBPUSD 2026-10-28 18:00, +10.9 pips:** the backtest entered at the 17:55 bar close (1.32988 + spread). By 18:00:09 the market had jumped (18:00 bar high 1.33114). The calendar can't say why: **economic_events ends 2026-10-18**, so the cause is UNKNOWN.
  - XAUUSD 2026-10-16 13:45, +15.2 pips ($1.52); XAUUSD 2026-10-13, +7.5 pips ($0.75): normal gold movement within seconds.

### 2.4 Exact path to the live result
- live +3.244R (matched) = backtest +8.719R − 0.901 (manual) − 2.319 (pre-Friday rule) + 0.430 (open) − 2.685 (execution, of which entry −2.41, exit −1.155, backtest cost model +0.95, denominator −0.056, swap −0.018, residual +0.004).
- Live total with R +2.242 = +3.244 − 1.002 (live-only GBPUSD).
- Backtest total +6.794 = +8.719 + 1.547 (skipped by live) − 4.575 (books not yet live) + 1.103 (3 not generated live).

---

## 3. The three unexplained backtest trades

### 3.1 fib618 GBPUSD long: signal 2026-10-16 09:00 UTC, backtest trigger Sunday 2026-10-18 21:05 UTC (backtest R +3.087)
- **Engine log** (`live_events`, matched line by line by `logs/bensim_live.log`):
  - "TIME EXIT EURUSD #2015405339" was retried every 5 min from **18:10 to 18:25 UTC**, each failing with retcode 10018 (market closed);
  - after that **no line at all until 2026-10-19 05:10:52** ("new day 2026-10-19"). The new-day marker normally appears at Prague midnight, 22:00:08 UTC on every other day (2026-10-15…10-27).
- **MT5 journal 20261018.log:** the same close attempts at 21:10-21:25 local (= 18:10-18:25 UTC), then **"connection to ICMarketsSC-Demo lost" at 21:26:06 local (18:26 UTC)**, with no further line that day. The 20261019 log resumes with "authorized … 08:08:20" local (05:08 UTC); the engine's next line is 05:10.
- **The broker closed EURUSD #2015405339 by SL at 21:01 UTC** (deal), with nothing from the engine.
- **Slot:** no bensim position open at 21:05 (occupancy 0). **Strategy:** enabled. **Broker rejection:** none (no order).
- **Verdict: MISSED.** Neither the engine nor the terminal was cycling or connected from ~18:26 UTC on 10-18 to ~05:08 UTC on 10-19; no cycle ran at 21:05.
  - Confidence high for "not cycling" (two independent logs).
  - The *cause* (PC sleep, network or hang) is UNKNOWN. No restart was logged at 05:10; the process resumed.

### 3.2 fib618 XAUUSD short: signal 2026-10-23 14:15 UTC, backtest trigger Monday 2026-10-26 03:15 UTC (backtest R −0.983)
### 3.3 imbalance XAUUSD short: same signal bar, backtest trigger 2026-10-26 03:25 UTC (backtest R −1.001)
- **Engine log:**
  - running before: "new day 2026-10-26" at 2026-10-25 23:00:08 UTC (on time; Prague is UTC+1 after 10-25); FLOW at 00:00:36; CRYPTO PAPER at 00:05:18;
  - **no line from 00:05 until 07:09** (FLOW error). That's normal for a quiet night: no positions, no signals, so nothing is logged;
  - no restart until 07:42 (after bridge 503 errors from 07:30).
- **MT5 journal 20261026.log** (local UTC+2): no disconnect logged before 09:05 local (07:05 UTC); then "connection lost", reconnect, terminal exit 09:25 local, restart 09:31 local.
- **Slot:** no bensim position open (occupancy 0). **Strategies:** both enabled (fib618 since start; imbalance book since 10-20). **Broker rejection:** none (no order).
- **A specific sensitivity (H, verified on DB bars):** at the triggers the setup was **46 and 47 M15 bars old against the 48-bar expiry** (`strategy.plan` L313-L330, `plan_bars = 48`). It sits right at the edge.
  - The DB bars (downloaded later) contain one weekend gap (Friday close → Sunday 22:00).
  - **If the live terminal held even 1-2 more M15 bars for XAUUSD at that moment** (e.g. around the Friday daily break or the Sunday reopen), the setup would already have expired live. This can't be checked: the live engine doesn't store the bars it used.
- **Verdict: UNKNOWN** ("never generated live" is the most likely class). The engine was very probably running (no gap in markers, no disconnect). The data difference can't be proven. Not "missed due to downtime" (no evidence), not "rejected" (no record), not "slot" (empty).

---

## 4. Live risk-enforcement matrix (actual code)
| Book (entry path) | Daily loss | Drawdown | Max positions | Pair duplication | Currency exposure | Per-trade risk | Minimum stop |
|---|---|---|---|---|---|---|---|
| fib618 (`live.decide` L305-329 → `guard.check`) | ✅ guard worst-case day + flatten (DEMO 10 %) | ✅ `risk_scale` (DEMO: never triggers) | ✅ DEMO 6 | ✅ `SYMBOL_ALREADY_OPEN` vs all magics 770101-770199 | ✅ `CURRENCY_SIDE_FULL` (DEMO 99 ≈ off) | 0.5 % × initial × confirmation (0.5 if 2/3) × scale | ✅ plan: stop ≥ 8 × cost (strategy.py L273) |
| bennett (`live.decide_bn` L280-302) | ✅ same | ✅ same | ✅ same | ✅ same | ✅ same | 0.5 % × 0.5 | ✅ plan: 8 × cost (bennett_strategy.py L369, L381) |
| imbalance, enigma (`books._send` L217-243) | ✅ | ✅ | ✅ | ✅ + `books.pick` one per pair | ✅ (≈ off) | 0.5 % × 0.5 | ✅ plan (strategy.py L273; fvg_strategy.py L93) |
| sflow (`books._send`) | ✅ | ✅ | ✅ | ✅ | ✅ (≈ off) | 0.5 % × 0.5 | ❌ **none** (only R:R ≥ 1, price inside plan) |
| parallax, cta (`books._send`) | ✅ | ✅ | ✅ | ✅ | ✅ (≈ off) | 0.5 % × 0.5 | ❌ none |
| flow ML / AM (`flow_live.step` L181-249) | ✅ `guard.flatten_now` only (L200) | ✅ `risk_scale == 0` only | ❌ **not checked** | ✅ own check vs all bensim magics (L194, L209, L238) | ❌ **not checked** | ❌ **fixed lots** 0.8 / 2.5, disaster stop 145 / 68 bp (≈ 1.3 % / 1.95 %) | n/a (fixed bp stop) |
| manager (`manager.open_trade` L72-89, CLI) | ✅ full `guard.check` vs all bensim positions | ✅ | ✅ (+ own max 2) | ✅ | ✅ (≈ off) | 0.25 % × initial | ❌ none |
| manual mobile/terminal closes | n/a (closes only) | — | — | — | — | — | — |

**Global controls:**
- The **daily flatten** in `live.cycle` closes **every** bensim magic (L195-196 → `close_all` L142-145).
- The **Friday 20:00 UTC close** closes every bensim magic (L198-199).
- The **kill switch** (`bensim.STOP`) is checked by all entry paths (fib618/Bennett `halted`, books `halted`, flow `blocked`, manager assert).

**Durable audit records:**

| Event | Stored in | Durable? |
|---|---|---|
| fib618/Bennett signal + decision + broker result | `live_signals` / `live_signals_bennett` (PK symbol, signal_time, dir) | ✅ |
| books | `live_books` | ✅ |
| flow | `live_flow` | ✅ |
| manager | `manager_actions` | ✅ |
| engine closes (time, Friday, flatten, cta trail) | `live_events` text lines only | ⚠️ |
| broker SL/TP exits and user manual closes | **MT5 deals only** (no copy in the DB) | ⚠️ |

---

## 5. Logging and reproducibility proposal (NOT implemented)

| Record | Exists today | Missing |
|---|---|---|
| Engine start | `live_events` "live engine start …" (text: params, symbols, books, risk) | git commit, dirty flag, code hash, env snapshot, guard cfg |
| Engine stop / crash | "cycle (n in a row)" errors; nothing on a clean stop, sleep or hang | stop / exit record |
| Heartbeat / uptime | `live_state.heartbeat` (**latest only**, overwritten) | history (this is what made §3 hard) |
| Strategy configuration + hash | params text in the start line | structured `asdict(PARAMS)` + hash |
| Market-data timestamp | signal_time only | last bar time per symbol per cycle, plus a fingerprint of the bars passed to `plan()` |
| Signal generation | TRIGGERED rows only | (non-triggers aren't needed) |
| Rejection + reason | `decision` (SKIP_GUARD_*, SKIP_PAIR_OPEN, …) | ✅ adequate |
| Risk check result | the reason string | the inputs (opens, worst-case figure) |
| Order request | partly (price, sl, tp, lots in detail) | the full request |
| Broker response | `detail.result` (retcode, order, deal, price, volume) | ✅ |
| Fill price / volume | `detail.result` + MT5 deals | DB copy of deals |
| SL/TP modifications | none by the engine; manual changes only in MT5 | MT5 order history (the bridge has no endpoint) |
| Position close | text log (engine) / MT5 deals (broker, manual) | structured close record |
| Costs / realized P&L | MT5 deals | DB copy |

**Smallest implementation that makes every future live trade reproducible** (about 60-80 lines, all additive):
1. **`live_audit` table** (append-only: ts, run_id, kind, book, symbol, ref, payload jsonb).
   - At engine start, write `kind=start`: `registry.code_version()` (commit, dirty, sha256 of `bensim/*.py`, which already exists), `asdict` of every params object and the active guard cfg, non-secret `BENSIM_*` env.
   - Use `atexit` / the SystemExit path for `kind=stop`.
2. **A per-cycle heartbeat row:** newest bar time per symbol, equity, open tickets, cycle duration. ~288 rows/day.
3. **`run_id` and a data fingerprint** (newest bar time + hash of the window arrays given to `plan()`) added to the existing `detail` of every decision (fib618, Bennett, books, flow). A later replay can then prove "same data / different data", which would have resolved §3.2-3.3.
4. **Daily deals sync:** bridge `/deals` → a `live_deals` table, idempotent on deal ticket. Broker truth for SL/TP exits, manual closes, swap.

SL/TP **modification** history would need a new bridge endpoint (`history_orders_get`). That's a change to the live bridge, so it's listed separately and needs approval.

---

## 6. FXCM data eligibility (corrected coverage; no backtests run)
**Coverage** = M5 bars present / expected (weekdays × 288), from `coverage_pct_by_year.csv`:
- **2012-2022: 99-100 % on all 25 pairs.**
- 2023: 97 %.
- 2024: 91-94 %.
- 2025: 90-93 %.
- **2026: 64-65 %.**
- Holes are whole missing weeks (404s at download), not scattered bars (≤ 87 short gaps per year even in 2025).

**Common facts:**
- Bid OHLC only. The backtest adds spread itself, so bid-only is sufficient for every `plan()` strategy.
- **No strategy reads tick volume** (no reference in strategy, indicators, bennett_*, fvg, gf, orderflow, pullback, sflow, parallax or cta code), so its absence doesn't matter.
- **FXCM `spr` isn't IC Markets cost:** median 0.2 pip EURUSD vs 0.8-0.9 observed on IC; zero-spread artefacts up to 21 % (2020-21). Costs must come from `exec_profile`, with FXCM spread only as a relative profile.
- **XAUUSD isn't available.**
- Calendar for news work: 2021-09-26 → 2026-10-18 only.

| Strategy | Instruments needed (live set) | Timeframes (from M5) | Bid-only ok | Volume needed | FXCM spread usable as cost | Missing-data sensitivity | Suitable FXCM years |
|---|---|---|---|---|---|---|---|
| fib618 | EURUSD GBPUSD USDCHF USDCAD **XAUUSD ✗** | M5, M15, H1 | yes | no | no | setup ≤ 12 h + hold ≤ 50 h: skip trades whose window touches a hole | **2012-2023** (2024-25 with hole-skip) |
| imbalance | 7 incl. AUDUSD ✓, **XAUUSD ✗** | M5, M15, H1 | yes | no | no | as fib618 | 2012-2023 |
| enigma / H1 FVG | same 7 (XAU ✗) | M5, M15, H1, H4 | yes | no | no | open HTF FVGs span days → a hole can hide a fill | 2012-2023 |
| bennett | EURUSD GBPUSD USDJPY USDCHF USDCAD | M5, M15, 30m, H1 (1,300 bars ≈ 54 days), H4 | yes | no | no | **long context:** require no hole in the prior ~60 days | 2012-2022 (2023 marginal) |
| pullback | majors | M15, H1 | yes | no | no | short | 2012-2023 |
| orderflow | majors | M15, H1 | yes | no | no | short | 2012-2023 |
| gf | majors | D1, 12H, 4H, M5 (3,200 H1 bars ≈ 133 days) | yes | no | no | **very long context** | 2012-2022 |
| sflow | 7 (XAU ✗) | H1, M5 | yes | no | no | 5-day wait + 10-day hold | 2012-2023 |
| parallax | 7 (XAU ✗) | D1 NY-close | yes | no | no | a missing week = 5 missing D1 bars (SMA/BB break) | 2012-2023 |
| cta | 7 (XAU ✗) | D1 NY-close, SMA200 | yes | no | no | 120-day holds; SMA200 needs continuity | 2012-2023 |
| flow ML | 25 pairs H1 + yields | H1 | yes | no | **yes as a model *feature*** (it was trained on the FXCM median spread) | holes = missing hourly features | 2012-2023 |

---

## 7. R1/R2 generator readiness (`bensim/r12_trades.py`, `r2_noise.py`; inspected, not changed)
| # | Generator (actual code) | Corrected v2 pre-registration / live | Effect |
|---|---|---|---|
| 1 | `STRATS["enigma"] = (books.ENIGMA, "htf_fvg", False)`, same for imbalance → `replace(ex, friday_close_utc=-1)` | all three close Friday 20:00 UTC, no entries Friday ≥ 20:00 (`live.close_all`, since 2026-10-20 for books) | **produces trades impossible live:** (a) held over weekends, (b) entered Friday 20:00-21:00 UTC |
| 2 | `r2_noise.FRIDAY = {"fib618"}` | horizon must include the Friday close for all three | TM horizon too long for enigma/imbalance |
| 3 | `SYMS` = 6 symbols for all three | live: fib618 = EURUSD GBPUSD USDCHF USDCAD XAUUSD (no USDJPY); books = 7 incl. AUDUSD | a pre-registered design choice (common set), but **not live**. Must be stated as such, or changed by pre-registration |
| 4 | time exit = 600 M5 bars (`backtest._simulate`) | live = 50 h wall-clock (`live.py` L209-215, books `_exits` L197) | differs over holidays / data gaps (small) |
| 5 | strategies independent | live one-per-pair across books, fib618 priority, guard | by design for R1/R2 (strategy-level question); fine if stated |
| 6 | costs `observed_account` | live shortfall measured at ≈ −0.10R/trade (§2.3) beyond the model; weekend-reopen spread unmodelled | add a −0.10R sensitivity, or note as a limitation |
| 7 | USDCAD from 2024-02-06 | v2 states it | none (the generator just finds no earlier bars) |
| 8 | `weekly_check.LIVE` for fib618 | `live.PARAMS` | **identical** (same `Params(zone_mode="fib", confirm_any=(killzone, poi, sweep), confirm_min=2)`) |

**To fix before R1/R2 can run (after approval):**
- items 1-2: Friday close for all three in both files;
- item 3: confirm the symbol-set decision in the pre-registration;
- item 6: decide whether to add the shortfall sensitivity;
- then re-record the code hash in the registry.

---

## 8. Outstanding unknowns and decisions

**UNKNOWN:**
1. Why fib618/imbalance XAUUSD on 2026-10-26 weren't generated live (most likely 1-2 bar data difference at the 48-bar expiry; unprovable without stored live bars).
2. The cause of the 2026-10-18 18:26-05:08 UTC outage (sleep, network or hang).
3. The reason for the GBPUSD 2026-10-28 18:00 jump (calendar ends 2026-10-18).
4. SL/TP modifications (no history available).
5. Partial fills (all 38 matched fills equal planned lots, so none observed).

**Decisions needed:**
1. Approve the logging proposal §5 (1-4)? Item 4's SL/TP history needs a bridge change.
2. Refresh the economic calendar beyond 2026-10-18 (an `events.py` scrape; a data change, so it needs approval).
3. Fix `r12_trades.py` and `r2_noise.py` (§7 items 1-2) and confirm the symbol set (item 3).
4. The FXCM use policy: 2012-2023 only, with hole-skipping; and no FXCM spread as a cost.
5. The operational proposals already submitted (parallax/cta, guard, flow sizing, sflow stop/lot cap), now with the enforcement gaps of §4 (flow: no max-open/currency check; sflow/parallax/cta/manager: no minimum stop).
