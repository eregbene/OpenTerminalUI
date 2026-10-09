# Imbalance Strategy: Learn 2024 → Test 2025 → Test 2026

Built 2026-10-08. Code: `bensim_research/imb_patterns.py` (trade pool from `imb_wf.py`); results in `data/research/imb_patterns_report.json`.

**Universe:**
- The video's entry: first retest of an armed zone, target = the nearest opposite zone.
- Timeframes M30/H1/H4; instruments EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, XAUUSD.
- Costs included.

**Procedure** (fixed before running):
- Exclude a bucket only if it loses on average, is worse than the rest with Welch t ≤ −2, and has at least 50 trades.
- Keep at most the 5 strongest exclusions.
- Test them unchanged on the next period.

## What happened in 2024 (7,596 trades, −0.15R/trade overall)

| Feature | What 2024 showed (net R per trade) |
|---|---|
| Timeframe | H1 −0.03 · H4 −0.10 · **M30 −0.22** |
| Stop size | **≤ 8 spreads −0.22** · 8–15 −0.07 · 15–25 −0.12 · > 25 −0.13 |
| Direction | buy −0.09 · **sell −0.21** |
| Instrument | USDJPY −0.00 · USDCAD −0.07 · EURUSD −0.13 · XAU −0.13 · USDCHF −0.18 · AUDUSD −0.25 · **GBPUSD −0.29** |
| BOS speed | BOS within 5 bars of the zone −0.13 · **BOS > 20 bars later −0.55** |
| Session (entry, UTC) | Off-hours 20–24h **+0.13** · London −0.10 · Asia −0.13 · NY −0.23 · **Overlap 12–16h −0.23** |
| Weekday | Tue −0.06 best · Fri −0.24 worst |
| HTF trend aligned | no help (aligned −0.19 vs not −0.12) |
| Killzone / strong displacement / extreme zone | small differences, \|t\| ≤ 1.1 |
| Zone height, run length, zone age, reward:risk | no meaningful difference |

> **Correction (2026-10-09):** an earlier version of this page reported 2025 at +0.109R. A gap-fill bug had measured R
> against the *fill* instead of the planned stop, so a few trades filled almost at their stop showed ±50R. With R measured
> against the planned stop, 2024 no longer selects the "no sells" rule and the 2025 result drops to +0.017R. All numbers
> below are corrected.

## Step 1: rules learned on 2024, tested blind on 2025

Rules: exclude M30 · exclude BOS > 20 bars after the zone · exclude stops ≤ 8 spreads · exclude GBPUSD.

| | Trades | Win % | Net R/trade | Total R | t |
|---|---|---|---|---|---|
| 2024 all (learn) | 7,596 | 20.7 | −0.152 | −1,157 | −5.5 |
| 2024 filtered (learn, in-sample) | 1,895 | 22.9 | −0.006 | −11 | −0.1 |
| **2025 all (test)** | 8,029 | 22.4 | **−0.103** | −829 | −4.0 |
| **2025 filtered (test, blind)** | **2,106** | 24.0 | **+0.017** | **+35** | 0.3 |

## Step 2: rules learned on 2024+2025, tested blind on Jan–Aug 2026

Rules: exclude stops ≤ 8 spreads · exclude M30 · exclude HTF-aligned · exclude AUDUSD · exclude GBPUSD.

| | Trades | Win % | Net R/trade | Total R | t |
|---|---|---|---|---|---|
| 2024–25 filtered (learn, in-sample) | 1,812 | 23.3 | +0.010 | +18 | 0.2 |
| **2026 all (test)** | 4,926 | 22.5 | **−0.093** | −460 | −2.9 |
| **2026 filtered (test, blind)** | **525** | 25.0 | **+0.105** | **+55** | 1.0 |

## What the data shows

- **Both blind tests improved on the unfiltered strategy,** by about +0.12R per trade in 2025 and +0.20R in 2026. Only 2026 ended clearly positive. 2025 is about break-even.
- **Neither result is statistically significant** (t 0.3 and 1.0).
- **Three rules were learned in both steps:**
  1. No M30 zones.
  2. Stop must be more than 8× the spread.
  3. No GBPUSD.

  The first two follow directly from costs, so they're the most trustworthy.
- The internet advice "trade with the HTF trend" was *not* supported: HTF-aligned zones did worse in 2024–25.
- Best and worst sessions in 2024: off-hours 20–24 UTC best, London/NY overlap worst.

## Step 3: 2024–2026 findings applied blind backwards (2022–23, then 2018–21)

Net R per trade. **2018–21 was never used for any choice.**

| Rule set | 2018–21 (blind) | 2022–23 | 2024 | 2025 | 2026 Jan–Aug |
|---|---|---|---|---|---|
| Unfiltered video rules | −0.151 (n=26,956) | −0.108 | −0.152 | −0.103 | −0.093 |
| CORE: no M30, stop > 8 spreads, no GBPUSD | −0.082 (n=6,232) | −0.029 | −0.023 | +0.007 | +0.012 |
| CORE + no entries 12–20 UTC (overlap/NY) | −0.060 (n=3,165) | +0.072 | +0.047 | +0.001 | +0.067 |
| … + skip HTF-trend-aligned zones | −0.040 (n=1,697) | +0.069 | +0.170 | +0.012 | +0.116 |
| … + skip the video's "extreme" zones | −0.019 (n=1,148) | +0.087 | +0.101 | −0.036 | +0.050 |

How the session and HTF rules were chosen: both were weak spots in 2024 *and*, independently, in 2022–23 (NY session t −2.5), before they were tested on 2018–21.

**What the data shows:**
- **Every rule helps in every period,** including the blind 2018–21. The losses shrink step by step from −0.15R to −0.02R per trade. The rules are real improvements, not noise.
- **But even the best version is only break-even before 2022,** and small-positive (+0.0 to +0.17R, never significant) from 2022 onwards. The core edge is too small to beat costs on its own.
- Surprises compared with the usual advice: the London/NY overlap and NY session are the *worst* times; HTF-aligned zones do *worse*; the video's "extreme zone" rule does *not* help.
- The best candidate for forward paper-testing: CORE + no 12–20 UTC + skip HTF-aligned. About 450–650 trades a year across 7 instruments on H1/H4. Expect roughly break-even to slightly positive, not a strong edge.
