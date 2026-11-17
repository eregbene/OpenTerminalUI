# Stage 4: seven active strategies with intelligence assistance (implementation plan and approval package, 2026-11-06)

## What is running now (no approval was needed: nothing in the DEMO engines changed)

- **Intelligence Advisor:** `bensim_phase20_dev/intel/advisor.py`, a single background process.
  - BelowNormal priority, one cycle per M5 close +90 s;
  - about 42 s per cycle at backfill (much less once caught up), about 190 MB of memory, 1.5 GB cap;
  - bridge calls are throttled, and it uses no LLM.
  - **Stop:** create `bensim_phase20_dev\intel_data\ADVISOR_STOP`.
- **Read-only:** there is no order, close or modify path; tests scan the source. It reads the engine's own signal tables and the account.
- **Agents** (one shared, validated bar cache):
  - currency strength and dispersion;
  - market regime (`market_intel.regime`);
  - market structure H1 / H4 / M30 (`market_intel.structure`, which reuses `bensim.indicators`, so there is no second engine);
  - expected move vs cost (`skills.expected_move`, `skills.cost`);
  - liquidity and FVG (`skills.liquidity`);
  - news (ForexFactory calendar; caution only; outage gives UNKNOWN);
  - portfolio risk (broker tick specs; unknown risk is never zero);
  - EURUSD overnight move (for Flow AM);
  - Manual Intervention Analyst (also snapshots SL/TP every cycle).
- **Per signal it records:**
  - the original engine decision;
  - FAVOURABLE / CAUTION / UNFAVOURABLE, from **strategy-specific** rules, each finding tagged with its evidence (round-4 numbers or "theory");
  - every agent's output and warnings;
  - the proposed caps check, observational;
  - real risk in $ and % of equity, and the lot size that 0.5% risk would give.
- **Two separate outcome views**, never added together:
  - **A** = the real DEMO result of sent signals;
  - **B** = a simulation of **every** signal, including skipped ones, under the strategy's own exits.
- **Output:**
  - journal `intel_data/advisor.sqlite` (append-only: update and delete are refused by triggers);
  - report `intel_data/ADVISOR_REPORT.md`, refreshed hourly;
  - heartbeat `intel_data/advisor_heartbeat.json`.
- **Tests:** 21 pass (8 advisor, 5 manual analyst, 8 skills).

**The 7 strategies keep trading exactly as before.** The classification has not and cannot change a trade.

## Backfill, 10 Oct to 6 Nov (154 signals, 149 resolved). Too small to judge anything.

| Strategy | Signals | FAV / CAU / UNF | B: sim, all vs without UNFAVOURABLE |
|---|---|---|---|
| fib618 | 41 | 5 / 34 / 2 | −0.10R vs −0.17R |
| Flow ML | 52 | 3 / 44 / 5 | +0.75 bp vs +1.51 bp (the late-hour rule removes the losers so far) |
| Flow AM | 17 | 0 / 17 / 0 | +2.64 bp |
| imbalance | 16 | 0 / 8 / 8 | +0.87R vs +0.13R (UNFAVOURABLE did *better* so far, n = 8) |
| sflow | 20 | 0 / 12 / 8 | +0.64R vs +1.56R |
| enigma / Bennett | 5 / 2 | 0 / 5 / 0 and 0 / 2 / 0 | n too small |

**Rule:** no filter is proposed for live use until each strategy has at least 30 resolved signals per class, tested out of sample.

---

## Needs your approval

These are separate items; each needs its own go. Each one changes the live engine and therefore needs an engine restart.

### Item 1: CTA and parallax to shadow-only
- **Change:** `BENSIM_BOOKS=imbalance,enigma,sflow` in the engine environment (today the default is all five books). The advisor would then log CTA / parallax signals as hypothetical trades only.
- **Impact:**
  - CTA and parallax stop placing DEMO orders;
  - their open positions are unaffected (none open now);
  - the other 7 strategies are unchanged.
- **Rollback:** remove the variable and restart.

### Item 2: risk-based sizing for Flow ML and Flow AM (0.5% of equity)
- **Change:** use the existing, tested `risk/flow.py` sizing (phase20) instead of fixed 0.8 / 2.5 lots. Lots = 0.5% of equity / (stop distance × broker tick value / tick size), rounded down to the lot step. A missing broker spec means no order.

| Book | Lots now | Risk now at the disaster stop | Lots at 0.5% | Risk |
|---|---|---|---|---|
| Flow ML EURUSD / EURJPY | 0.8 | $1,296 (1.31%) | ≈ 0.30 | ≈ $496 |
| Flow ML USDJPY / USDCAD | 0.8 | $1,160 (1.17%) | ≈ 0.34 | ≈ $496 |
| Flow ML CADJPY | 0.8 | $824 (0.83%) | ≈ 0.48 | ≈ $496 |
| **Flow AM EURUSD** | **2.5** | **$1,899 (1.91%)** | **0.65** | ≈ $496 |

- **Impact on the last 4 weeks:** Flow P&L scales down by the same factor, so its wins and losses would both be about 2.6–3.8× smaller. The edge per trade in bp is unchanged.
- **This does not increase risk.** It reduces it to the configured 0.5%.

### Item 3: portfolio protections (deterministic, refuse new entries only; exits and stops are never blocked)
- **Rules:**
  - aggregate open risk ≤ 2.5% of equity (initial stops, broker tick specs);
  - ≤ 2 open legs per currency per side, across all books including manual and manager trades;
  - an entry whose risk can't be computed is refused.
- **Replay on the 96 real DEMO trades since 10 Oct** (`reports/round4/r4_caps_replay.json`):

| Scenario | Blocked | P&L of blocked trades | Kept net | Max open risk |
|---|---|---|---|---|
| Caps with **today's sizing** | 20 (13 aggregate, 8 currency-leg) | −$1,645 | +$3,083 | 2.42% |
| Caps **+ Item 2** | 10 (all currency-leg; USD-short 7) | −$793 | +$1,441 | 1.73% |

  In both cases the blocked trades lost money in total. The one blocked winner was fib618 (+$497).
- **Why today's sizing breaches so often:** without Item 2, Flow's fixed lots alone use 1.2–1.9% each, so two Flow trades already breach 2.5%. **Recommended order: Item 2 before Item 3.**
- **Right now** the 3 open Flow positions sum to **4.06%** at their stops, already above the proposed cap.

### Item 4 (later): an intelligence veto for a single strategy
Not proposed now. It becomes eligible only after at least 30 resolved signals per class for that strategy show that UNFAVOURABLE signals do worse out of sample. Even then it applies per strategy, behind a flag that defaults to off.

## Deployment procedure (when you approve items 1–3)
1. Take a snapshot of the live files (pattern `baseline_originals/`) and record their SHA-256.
2. Apply only the approved items, each behind an environment variable that defaults to the current behaviour.
3. Run the unit tests and a dry-run cycle (`BENSIM_LIVE_FROM` in the future), compared against the current engine on the same bars.
4. Restart in a quiet window you choose, never with Flow AM open (09:00–12:00 UTC) or near a news release.
5. Watch the advisor and the engine heartbeat for 24h.
6. **Rollback** = restore the snapshot files and restart. That takes about 1 minute; positions keep their broker stops.

## Not done (needs a decision or data)
- **Grok news provider:** needs your xAI key and a daily budget. Until then, news comes from the calendar only.
- **Shadow v0.3:** needs the read-only investor terminal at `E:\MT5\Bensim-quotes`.
- **Advisor autostart after a reboot:** not installed. It currently has to be started by hand, or added to the existing autostart task with your OK.

---

## DEPLOYED: 2026-11-06 14:16:55 UTC (user GO: "go but i need all 7 strategys included")

**Restart:** after NFP and with no high-impact news within an hour. Engine pid 23060 → 46752, bridge → 35104, both set to High priority; about 53 s downtime. The open EURJPY Flow ML position kept its broker stop.

**Items applied:**
- Flow ML late-hour skip (20–00 UTC);
- Flow sizing at 0.5% risk (ML ≈ 0.30–0.48 lots, AM ≈ 0.65 lots);
- guard: open-risk cap 2.5% + max 2 legs per currency side + unknown risk refused;
- CTA and parallax recorded as `SHADOW` and not sent.

**Still trading:** fib618, Bennett, Flow ML, Flow AM, imbalance, enigma, sflow.

**Checks before the restart:**
- guard selfcheck;
- `bensim/test_live.py` + `test_flow_live.py` (5 passed);
- books selfcheck;
- dry Flow sizing against the account.

**Backup:** `D:\Devops\trading\backups\deploy_2026-11-06_stage4\` (SHA256SUMS.txt).

**Rollback without a code change:** set `BENSIM_FLOW_RISK_PCT=0`, `BENSIM_FLOW_ML_SKIP_UTC=`, `BENSIM_GUARD_OPEN_RISK_PCT=100`, `BENSIM_GUARD_MAX_PER_SIDE=99` and `BENSIM_BOOKS=imbalance,enigma,sflow,parallax,cta`, then restart. Or restore the backup files.

**Known cosmetic issue:** the engine start line still prints "ml 0.8 lots am 2.5 lots" (old constants). Actual sizing comes from `BENSIM_FLOW_RISK_PCT`.
