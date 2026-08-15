"""Offline threshold-sweep + hypothetical-verdict analysis of the ALREADY-COMPLETED 4,542-candidate
EURUSD backtest (/app/eurusd_backtest_records.json). Read-only: never touches production config,
never re-runs candidates, never changes _MIN_SAMPLE_FOR_LIVE_INFLUENCE.

IMPORTANT ARCHITECTURAL NOTE (found while writing this): the real live gate has TWO independent
mechanisms that currently happen to both land on n=100:
  1. entry_intelligence._MIN_SAMPLE_FOR_LIVE_INFLUENCE = 100 (numeric ESS floor)
  2. statistics.reliability_label()'s ladder, whose "USEFUL" tier only starts at n=100 -- and
     entry_intelligence._RELIABLE_LEVELS = {"USEFUL","STRONG"} gates on THIS separately.
A hypothetical "lower the threshold to 50" is therefore not a single-parameter change: with the
reliability ladder untouched, ESS=50 still resolves to "LOW_CONFIDENCE", which is not in
_RELIABLE_LEVELS, so the real code would produce IDENTICAL verdicts (INSUFFICIENT) regardless of
what _MIN_SAMPLE_FOR_LIVE_INFLUENCE is set to below 100. For the simulation below to be
non-trivial, "qualifies at threshold T" is treated as ESS>=T with reliability ALSO hypothetically
relaxed to T (i.e. as if reliability_label's USEFUL cutoff moved to T) -- this is an approximation
of the real two-gate system, clearly flagged as such, not a claim about what today's code would do.

A second, unavoidable approximation: the persisted per-candidate records do not include
profit_factor or immediate_failure_rate (not captured by run_eurusd_backtest.py's evaluate_one()),
so _historical_score's pf_component and failure_component cannot be exactly reconstructed. The
simulated score below uses ONLY the expectancy and continuation-probability components (the two
fields that ARE persisted: predicted_expectancy_r, predicted_p_1r), with reliability_weight=1.0
(the "STRONG" weight) for anyone clearing the simulated threshold. This is a real approximation of
the production formula, not a re-run of it -- flagged wherever used.
"""
import json
import statistics as pystats
from collections import defaultdict
from datetime import datetime

with open("/app/eurusd_backtest_records.json") as f:
    records = json.load(f)

for r in records:
    r["_entry_dt"] = datetime.fromisoformat(r["entry_time"])
    r["_year"] = r["_entry_dt"].year

N_TOTAL = len(records)
THRESHOLDS = [10, 20, 30, 40, 50, 60, 75, 90]

# Simulated score/decision using ONLY the two persisted components (see module docstring).
def simulated_score(r):
    expectancy = r["predicted_expectancy_r"]
    p1r = r["predicted_p_1r"]
    if expectancy is None or p1r is None:
        return None
    expectancy_component = max(-1.0, min(1.0, expectancy / 2.0)) * 40.0
    continuation_component = p1r * 20.0
    raw = (expectancy_component + continuation_component) * 1.0  # simulated reliability_weight=STRONG
    return round(max(-50.0, min(50.0, raw)), 2)

def simulated_decision(score):
    if score is None:
        return "INSUFFICIENT"
    if score >= 15.0:
        return "SUPPORT"
    if score <= -15.0:
        return "OPPOSE"
    return "RANK_ADJUST"

def brier_and_calibration(subset):
    have_p1r = [r for r in subset if r["predicted_p_1r"] is not None and r["actual_reached_1r"] is not None]
    if not have_p1r:
        return None, None, None
    brier = sum((r["predicted_p_1r"] - (1.0 if r["actual_reached_1r"] else 0.0)) ** 2 for r in have_p1r) / len(have_p1r)
    mean_pred = sum(r["predicted_p_1r"] for r in have_p1r) / len(have_p1r)
    actual_rate = sum(1 for r in have_p1r if r["actual_reached_1r"]) / len(have_p1r)
    return round(brier, 4), round(abs(mean_pred - actual_rate), 4), len(have_p1r)

def win_rate(subset):
    resolved = [r for r in subset if r["actual_outcome_r"] is not None]
    if not resolved:
        return None
    return round(sum(1 for r in resolved if r["actual_outcome_r"] > 0) / len(resolved), 4)

def mean_r(subset, key):
    vals = [r[key] for r in subset if r.get(key) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None

def median_r(subset, key):
    vals = [r[key] for r in subset if r.get(key) is not None]
    return round(pystats.median(vals), 4) if vals else None

print("=" * 70)
print("PART 1 -- OFFLINE ESS THRESHOLD SWEEP (n=%d total candidates)" % N_TOTAL)
print("=" * 70)
sweep_results = {}
for T in THRESHOLDS:
    qualifying = [r for r in records if r["effective_sample_size"] >= T]
    n = len(qualifying)
    if n == 0:
        sweep_results[T] = {"n": 0}
        continue
    ess_vals = [r["effective_sample_size"] for r in qualifying]
    brier, cal_mae, brier_n = brier_and_calibration(qualifying)
    sim_scores = [simulated_score(r) for r in qualifying]
    sim_decisions = [simulated_decision(s) for s in sim_scores]
    support_n = sum(1 for d in sim_decisions if d == "SUPPORT")
    oppose_n = sum(1 for d in sim_decisions if d == "OPPOSE")
    support_records = [r for r, d in zip(qualifying, sim_decisions) if d == "SUPPORT" and r["actual_outcome_r"] is not None]
    oppose_records = [r for r, d in zip(qualifying, sim_decisions) if d == "OPPOSE" and r["actual_outcome_r"] is not None]
    false_support = round(sum(1 for r in support_records if r["actual_outcome_r"] <= 0) / len(support_records), 4) if support_records else None
    false_oppose = round(sum(1 for r in oppose_records if r["actual_outcome_r"] > 0) / len(oppose_records), 4) if oppose_records else None
    sweep_results[T] = {
        "n": n, "coverage_pct": round(100.0 * n / N_TOTAL, 2),
        "mean_ess": round(sum(ess_vals) / n, 2), "median_ess": round(pystats.median(ess_vals), 2),
        "expected_r": mean_r(qualifying, "predicted_expectancy_r"), "realized_r": mean_r(qualifying, "actual_outcome_r"),
        "win_rate": win_rate(qualifying), "brier": brier, "brier_n": brier_n, "calibration_mae": cal_mae,
        "sim_support_n": support_n, "sim_oppose_n": oppose_n,
        "false_support_rate": false_support, "false_oppose_rate": false_oppose,
    }
    print(f"\nESS >= {T}:")
    for k, v in sweep_results[T].items():
        print(f"  {k}: {v}")

print("\n" + "=" * 70)
print("PART 1b -- CHRONOLOGICAL BREAKDOWN (by year, ESS>=20 and ESS>=50 only)")
print("=" * 70)
years = sorted(set(r["_year"] for r in records))
for T in (20, 50):
    print(f"\n--- ESS >= {T} by year ---")
    for y in years:
        subset = [r for r in records if r["_year"] == y and r["effective_sample_size"] >= T]
        if len(subset) < 10:
            print(f"  {y}: n={len(subset)} (too few to report)")
            continue
        brier, cal_mae, _ = brier_and_calibration(subset)
        print(f"  {y}: n={len(subset)} realized_r={mean_r(subset,'actual_outcome_r')} expected_r={mean_r(subset,'predicted_expectancy_r')} win_rate={win_rate(subset)} brier={brier} cal_mae={cal_mae}")

print("\n" + "=" * 70)
print("PART 1c -- BREAKDOWN BY STRATEGY / DIRECTION / REGIME / SESSION (ESS>=20, n>=30 only)")
print("=" * 70)
for dim in ("strategy", "direction", "regime", "session"):
    print(f"\n--- by {dim} (ESS>=20) ---")
    groups = defaultdict(list)
    for r in records:
        if r["effective_sample_size"] >= 20:
            groups[r[dim]].append(r)
    for key, subset in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(subset) < 30:
            continue
        print(f"  {key}: n={len(subset)} realized_r={mean_r(subset,'actual_outcome_r')} win_rate={win_rate(subset)}")

print("\n" + "=" * 70)
print("PART 2 -- HYPOTHETICAL VERDICT SIMULATION (offline only, does not touch production)")
print("=" * 70)
for T in (20, 30, 50, 75, 90):
    qualifying = [r for r in records if r["effective_sample_size"] >= T]
    sim_decisions = [simulated_decision(simulated_score(r)) for r in qualifying]
    non_qualifying_n = N_TOTAL - len(qualifying)
    support = sum(1 for d in sim_decisions if d == "SUPPORT")
    oppose = sum(1 for d in sim_decisions if d == "OPPOSE")
    neutral = sum(1 for d in sim_decisions if d == "RANK_ADJUST")
    print(f"\nThreshold {T}: SUPPORT={support} OPPOSE={oppose} RANK_ADJUST(neutral)={neutral} INSUFFICIENT={non_qualifying_n}")

print("\n" + "=" * 70)
print("PART 3 -- WHY DOES RAW N>=100 COLLAPSE BELOW ESS 100? (quantifying the gap)")
print("=" * 70)
raw_ge_100 = [r for r in records if r["raw_neighbor_count"] >= 100]
print(f"Candidates with raw_neighbor_count >= 100: {len(raw_ge_100)}/{N_TOTAL} ({100*len(raw_ge_100)/N_TOTAL:.1f}%)")
if raw_ge_100:
    ratios = [r["effective_sample_size"] / r["raw_neighbor_count"] for r in raw_ge_100 if r["raw_neighbor_count"] > 0]
    ratios.sort()
    print(f"  Among those: median ESS/raw ratio = {pystats.median(ratios):.4f}")
    print(f"  95th pct ESS/raw ratio = {ratios[int(len(ratios)*0.95)]:.4f}")
    print(f"  min ESS/raw ratio = {ratios[0]:.4f}  max = {ratios[-1]:.4f}")
    ess_lt_20 = sum(1 for r in raw_ge_100 if r["effective_sample_size"] < 20)
    ess_lt_50 = sum(1 for r in raw_ge_100 if r["effective_sample_size"] < 50)
    ess_ge_100 = sum(1 for r in raw_ge_100 if r["effective_sample_size"] >= 100)
    print(f"  raw>=100 but ESS<20: {ess_lt_20} ({100*ess_lt_20/len(raw_ge_100):.1f}%)")
    print(f"  raw>=100 but ESS<50: {ess_lt_50} ({100*ess_lt_50/len(raw_ge_100):.1f}%)")
    print(f"  raw>=100 and ESS>=100: {ess_ge_100} ({100*ess_ge_100/len(raw_ge_100):.1f}%)")
ess_all = [r["effective_sample_size"] for r in records]
print(f"\nOverall ESS distribution: min={min(ess_all):.1f} median={pystats.median(ess_all):.1f} max={max(ess_all):.1f} mean={sum(ess_all)/len(ess_all):.2f}")

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
