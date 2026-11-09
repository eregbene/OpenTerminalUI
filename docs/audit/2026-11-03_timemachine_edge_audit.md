# Bensim: Time Machine and edge audit (2026-11-03)

Read-only audit. Nothing was changed in live gates, risk, execution, paper or live.
New numbers in this report come only from files already on disk (`logs/tm_check_2026-07-26_fx.csv`, 155 trades, Jul 26 – Oct 26 2026) and read-only DB queries. No new backtest was run.

## Verdict in one paragraph

No Bensim strategy has statistically defensible out-of-sample evidence of an edge.
- **The 3-month portfolio isn't significant.** fib618, imbalance, enigma and Bennett together: +0.317R/trade net of spread and commission, day-clustered t = 1.40 (56 days). That's before swap, and it includes weekend holds the live engine doesn't do. Without the weekend holds: +0.244R, t = 0.97.
- **The long-run tests are negative or flat.**
  - fib618: −0.048R on 25 pairs × 14 years.
  - imbalance base: +0.048R over 5 years, t = 0.96.
  - enigma over 5 years: about break-even.
  - Bennett setups: negative after costs.
- **The Time Machine "contrarian" effect has a simple cause.** The day-trader ("repo") Time Machine is effectively a **60-day momentum indicator**. Its favoured/opposed split matches "with/against the trailing 60-day trend" in 74 % of trades. The result comes from one pair (GBPUSD) and 3 weeks, and isn't significant once you count by day and account for how many splits were tried.

---

## 1. Skills and architecture dependency map

### 1a. Claude trading skills: there are 11, not 15
They are the `day-trader-skill` plugin v2.0.0. **None of them touch Bensim.** They're US-stock tools on free Yahoo data, standard library only.

| Skill | Script | Data | Writes | Used by |
|---|---|---|---|---|
| trader-scanner | scan.py orb/daily/universe | Yahoo chart 1d/5m, Nasdaq lists | trader/watchlist.json | entry |
| trader-news | news.py | Yahoo RSS + SEC EDGAR | trader/state/news | none: information only |
| trader-entry | entry.py check/watch | Yahoo 1d/5m | trader/entry.json | exit |
| trader-exit | exit.py plan | entry.json (+Yahoo for an ATR stop) | trader/plan.json | timemachine, budget |
| trader-timemachine | timemachine.py | Yahoo 60d/5m | trader/timemachine.json | none: advisory only |
| trader-budget | size.py | plan + config (+portfolio) | trader/orders.json | risk |
| trader-risk | check.py | orders + config + broker state | trader/state/risk.json | broker |
| trader-broker | ticket.py | risk verdicts | Alpaca MCP order | — |
| trader-alerts | alerts.py | Yahoo quotes | trader/prices.json | budget/risk prices |
| trader-analyst | facts.py | SEC XBRL | — | none |
| trader-pelosi | ptr.py | House Clerk filings | — | none |

There's also `backtest-expert`, which is guidance text only, with no code.

### 1b. Bensim (forex, MT5)
```
MT5 terminal ──► bensim.mt5_bridge (host, :8773) ──► bensim.live (fib618 LIVE + Bennett LIVE)   [running, PID 14600]
                                              └──► bensim.books (imbalance, enigma, sflow, parallax, cta; all ON by default)
                                              └──► bensim.tradeguard live (prop-firm guard)       [running]
Postgres `bensim` (candles M5 UTC bid, economic_events) ──► bensim.backtest.run_symbol ──► strategy.plan()/order_for()
                                                                    │                    (shared with live)
                                                                    └──► _simulate (costs, exits)
bensim.timemachine (null model / repo mode) ◄── load_m5 (same DB)   — information only; the manager gate was removed 2026-10-29
bensim.tm_check  = backtest trades ⨝ timemachine at each entry → logs/tm_check_*.{log,csv}
bensim.walk_forward, pattern_check.stat, meta.py  = the only statistics in bensim (t-stat, simple walk-forward, logistic meta-label)
archive/full-system-2026-10-09: backend/model_lab/{metrics,montecarlo}.py, core/backtest_robustness.py,
  historical_intelligence/strategy_robustness.py (PSR/DSR/ESS/bootstrap/drawdown duration) — NOT wired into bensim
```

### 1c. The 8 strategies

| Strategy | Code / params | Signal | Entry | Stop / target | Hold | Live? |
|---|---|---|---|---|---|---|
| fib618 | strategy.Params(zone_mode=fib, confirm_any=(killzone,poi,sweep), confirm_min=2) = weekly_check.LIVE | M15 Donchian break ≥0.3 ATR with ATR expansion; H1 trend agrees | market at the M5 close that touches the fib 0.618 of the break leg | stop at fib invalidation; ATR or liquidity target | 200 M15 bars | **yes** (live.py) |
| imbalance | strategy.Params() = books.IMB | same Donchian break | market at the M5 touch of the 3-candle-gap zone | zone far edge −0.1 ATR; target 4 ATR | 200 M15 | **yes** (books) |
| enigma | FvgParams(htf=H4) = books.ENIGMA | open H4 FVG tapped, then an M15 MSS plus a displacement FVG | M15 FVG CE | leg extreme; 2R | 200 M15 | **yes** (books) |
| H1 FVG | FvgParams() (htf=H1) | same as enigma on H1 | same | same | same | no |
| Bennett | bennett_strategy.LIVE | daily bias, H1 leg, OTE 62–79 % relief, then an M5 CHoCH | close | invalidation; min 2.75R, T2 | leg | **yes** (live.py, half risk) |
| pullback | PullbackParams() | M15 EMA20/50 side, close in band ±1 ATR, H1 not against | first M5 close after M15 | 1.2 / 2.5 ATR | 200 M15 | no (3m: −0.17R, t −3.8) |
| orderflow | OrderFlowParams(liquidity_ob) | Faiz OB liquidity / jeafx modes | limit near OB | fake-out extreme; 3R | 200 M15 | no (3m: ≈0 to −0.22R) |
| gf | GfParams() | D1 and 12H structure agree, 4H CHoCH on a close | first M5 after the 4H close | beyond the 4H pullback; 12H leg extreme | 480 M15 | no |

---

## 2. Missing or incorrectly connected components

| # | Finding | Effect | Severity |
|---|---|---|---|
| M1 | **The backtest has no Friday-close exit; live closes everything Friday 20:00 UTC** (live.py:198). Pending orders are cancelled on a weekend only if `weekend_cancel=True`. | 18 of 155 trades in the Time Machine sample held over a weekend and made **+15.7R (32 % of the profit)**. The backtest is not the live strategy. | **High** |
| M2 | **Swap/financing is not modelled for FX.** The rollover window only widens the spread (×4, 20:55–22:05). | 68 of 155 trades were held overnight. Net R is overstated by an unknown amount (often 0.01–0.1R per night at these stop sizes; triple on Wednesday). | **High** |
| M3 | **The candle DB ends 2026-10-26 08:30 UTC.** The live engine reads MT5 directly, so research data is 8 days stale. | "Last 3 months" can't reach today without a backfill from MT5 (real bars only). | Medium |
| M4 | **Spread is a constant estimate per symbol.** The docstring says "× hour-of-day profile", but the code only applies the rollover multiplier (backtest.py:14 vs 79-84). The DB has no spread column. | Asian-session and news-time spreads are understated. | Medium |
| M5 | **PSR/DSR/ESS/block-bootstrap/drawdown-duration code exists only in the archive branch.** Bensim reports a plain trade t-stat (pattern_check.stat). | Nothing corrects for overlapping trades, autocorrelation or multiple testing. | **High** (for decisions) |
| M6 | **No multiple-testing ledger.** Dozens of variants, filters and splits have been tried on the same Jul–Oct window (18-variant grid, confirmations, TP ladders, Time Machine splits…). | Any single t ≈ 2 found on that window is expected by chance. | **High** |
| M7 | **Time Machine horizon ≠ trade horizon.** The TM replays to Friday 20:00, while backtest trades exit by target, stop, or 200 M15 bars and run through weekends. | The TM odds are for a different trade than the one scored. | Medium |
| M8 | **The repo-mode pool mixes Sunday-open and rollover bars into time-of-day slots** (bars_by_slot pools all weekdays; the first bar after the weekend keeps the gap). | Extra volatility at the 21:00–22:00 UTC slots. Small calibration error. | Low |
| M9 | **"Gross R" isn't truly gross.** `r_gross` is after spread and slippage and before commission only. There's no mid-price, zero-cost R. | The gross/net comparison only shows the commission (avg 0.062R). | Low (labelling) |
| M10 | **Day-trader runner (today's paper test):** trader-risk runs without `--portfolio`, so every order is checked against a fresh $100k. Three passes can mean ~$150k of positions on a 4× margin paper account. News is recorded, not used. | Paper sizing can exceed the stated budget. Not changed (per your rules). | Medium |

### Cost-source table

| Cost | Value in backtest | Source |
|---|---|---|
| Spread | 0.1–0.3 pip FX, 1.2 gold (raw) × 4 at rollover | **Estimate** (no observed spread stored) |
| Commission | $7 per lot round trip | **Estimate from broker schedule** (IC Markets raw); not reconciled with fills |
| Slippage | 0.1 pip on market entries and stops | **Estimate** |
| Gap through stop | filled at the bar open | **Observed** (bars) |
| Swap/financing | 0 | **Unknown / missing** |
| Crypto funding | 0 | **Missing** (marked `ponytail:` in code) |

### Lookahead check

**Clean.**
- `plan()` only sees completed M15/H1/M5 bars: windows are cut by bar end ≤ now (backtest.py:162-166).
- Entries are at the trigger bar's close; exits are checked from the next bar on.
- A bar that touches both stop and target counts as a stop.
- The Time Machine pool uses only bars before `t0`.

**Selection bias.** The symbols (5 pairs plus gold) and the live-strategy choices were picked after looking at results, and the 3-month window was reused for picking. That's a selection bias, not lookahead.

---

## 3. Root cause of "favoured −0.20R, opposed +0.73R"

1. **What it measures.** Repo mode keeps each slot's 60-day average bar drift and compounds it over up to ~5 days of replayed bars. With small FX drift, the sign of `rp_avg_r` is mostly the sign of (trade direction × trailing 60-day trend). Measured: favoured = with the 60-day trend in **74 %** of trades. A plain "against the 60-day trend" split gives the same result: **+0.61R (75 trades) vs +0.04R (80)**. So this isn't a simulation finding. It's a 60-day momentum filter.
2. **It isn't a long/short or spread artefact.** Shorts and longs fall into both groups (favoured: 43 short / 26 long; opposed: 49 / 37).
3. **It's concentrated.**
   - GBPUSD "opposed" alone is ≈ +40R of the +63R.
   - 3 weeks (Jul 27, Sep 7, Oct 12) give ≈ +51R.
   - The rank correlation between `rp_avg_r` and R is only −0.09.
4. **It isn't significant.** The day-clustered t for the opposed group is 2.07. That was one of ≥ 6 splits tried in the same session (10 % gate, 4 target buckets, stop-in-first-hour buckets, favoured/opposed), all on a window that had already been used to pick strategies. After correction it isn't significant.
5. **There's a plausible regime reason.** Earlier work found that FX daily breakouts fade nearly every year, and that entries against H1 structure did better. Mean reversion at the multi-week horizon in Jul–Oct 2026 would produce exactly this. That makes it a **hypothesis**, not a finding. The stock version ran the other way in October (favoured +0.135R, 16 trades), which fits "it's the regime, not the tool".
6. **The default bensim mode can't predict by design.** It removes drift, so it's a null model. That's correct, and stated in its docstring. The removed 10 % gate failed for that reason.

**Conclusion.** It's not a bug in the replay code. It's model calibration (drift = momentum) plus regime plus small, clustered, reused-data statistics. Don't reverse signals. Test "against the 60-day trend" once, as a pre-registered hypothesis, on data that has never been looked at.

---

## 4. Individual strategy reports (from results already on file)

| Strategy | 3m Jul 26–Oct 26 (tm_check, net ex-swap) | Longer / other evidence | Status |
|---|---|---|---|
| fib618 (live) | 75 tr, +0.386R, PF 1.57, day-t 1.46 | 25 pairs × 14y −0.048R, 0/15 years positive; Apr–Jul 2026 −0.045R | **no edge** |
| imbalance | 55 tr, +0.050R, PF 1.06, day-t 0.15 | 5y +0.048R t 0.96, DD 26 % | **no edge** |
| enigma | 22 tr, +0.617R, PF 1.91, day-t 0.99 | 5y ≈ break-even | **no edge (n too small)** |
| Bennett | 3 tr | 5y setup DB negative after costs | **no edge** |
| H1 FVG | not in tm_check | earlier 3m runs only (data/bensim_fvg_3m_results.log) | untested on the common set |
| pullback | not in tm_check | 3m: 955 tr −0.17R, t −3.8 | **negative** |
| orderflow | not in tm_check | 3m variants 0.00 to −0.22R; best post-hoc +0.32R t 0.88 | **no edge** |
| gf | not in tm_check | only on her dates (Sep 25–Oct 13) | untested |

They have never been run on **one common dataset with identical costs**. That run is what you approve in step P2 below.

## 5. Combined portfolio (4 strategies, same file)

| | Trades | Avg R | Total R | PF | Trade-t | Day-t |
|---|---|---|---|---|---|---|
| All | 155 | +0.317 | +49.1 | 1.43 | 1.65 | 1.40 |
| Without weekend holds (≈ live behaviour) | 137 | +0.244 | +33.4 | 1.31 | 1.16 | 0.97 |

Swap would lower both. Not significant.

## 6. Statistical confidence and overfitting

- **3 months is far too short for any PSR/DSR to clear a threshold.**
  - At +0.25R per trade with SD ≈ 1.3R, you need about 110 independent trades for t = 2 before any correction.
  - With N ≈ 50–100 variants tried on this window, the Deflated Sharpe bar is roughly t ≈ 3, which needs about 250+ trades.
- **Overlap matters.** fib618 and imbalance share the same Donchian trigger, so their trades are correlated and the effective N is below the trade count.
- **The only long-sample results (5y / 14y) are negative or flat.** Every positive figure comes from the Jul–Oct 2026 window that was used to make decisions.
- **Overfitting risk: high.** The live configuration (2-of-3 confirmations, pair list) was picked on the same window it's now judged on.

## 7. Prioritised plan (each step waits for your OK)

| Priority | Step | Touches live? | Cost |
|---|---|---|---|
| **P0** | Backfill candles Oct 26 → now from MT5 (real bars only; gaps are reported, never filled) | no | minutes |
| **P0** | Backtest options matching live: `friday_close=20` exit plus weekend cancel; **swap from MT5 `symbol_info` swap_long/short (observed)**, triple on Wednesday; label every cost observed/estimated/unknown in the output | no (new options, default off) | ~1 h |
| **P1** | `bensim/validate.py`: port the archive scorecard (PSR, DSR with the variant count, ESS/lag-1 autocorrelation, block bootstrap, drawdown duration/recovery, by pair/session/regime). Reads trade CSVs only. Plus a **trial ledger** of every variant ever run, so DSR uses an honest N. | no | ~2 h |
| **P2** | Common run: 8 strategies × same 6 symbols × the same live-matching Exec, frozen params, last 3 months (your rule). This produces deliverables 4 and 5 properly. | no | ~30–60 min CPU at low priority |
| **P3** | One pre-registered test of "against the 60-day trend" (and the TM split) on **never-used data** (FXCM 2012–2021 or 2021–2025 MT5). It needs a period longer than 3 months, which is why it needs your explicit yes. | no | ~1 h CPU |
| **P4** | Decisions after P2/P3: keep or stop live books; Time Machine stays as an information tool, or becomes a filter only if P3 passes | **yes** (your call) | — |

Not proposed: new strategies, parameter tuning, signal reversal.
