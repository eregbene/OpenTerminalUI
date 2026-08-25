"""MTFAI1 V2 confidence-calibration audit (continuation) -- reads the JSONL produced by
scratch_mtfai1_v2_historical_reconstruction.py (point-in-time-safe, no look-ahead). Does NOT
re-run or touch the reconstruction. Answers, in order: threshold sweep, band analysis + rank
correlation, the specific 65-70 vs 70-75 hypothesis (chronological + per-symbol), RR-quality A/B
(recomputed from already-persisted raw inputs, no re-simulation needed), and component
attribution.
"""
import json
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta

INPUT_PATH = "/data/historical_intelligence/mtfai1_v2_reconstruction.jsonl"

THRESHOLDS = [None, 60, 65, 67.5, 70, 72.5, 75, 77.5, 80]
BANDS = [(0, 60, "<60"), (60, 65, "60-65"), (65, 70, "65-70"), (70, 72.5, "70-72.5"),
         (72.5, 75, "72.5-75"), (75, 80, "75-80"), (80, 101, "80+")]

_REWARD_RISK_FLOOR = 1.5
_REWARD_RISK_TARGET = 3.0


def load_rows():
    rows = []
    try:
        with open(INPUT_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        return []
    return rows


def outcome_r(row, use_net=True):
    if use_net and row.get("net_outcome_r") is not None:
        return row["net_outcome_r"]
    return row.get("outcome_r")


def resolved(rows):
    return [r for r in rows if r.get("resolution_status") == "RESOLVED" and r.get("outcome_r") is not None]


def unique_setups(rows_list):
    rows_sorted = sorted(rows_list, key=lambda r: (r["symbol"], r["at"]))
    setups, current = [], None
    for r in rows_sorted:
        at = datetime.fromisoformat(r["at"])
        key = (r["symbol"], r["direction"])
        if current and current["key"] == key and (at - current["last_at"]) <= timedelta(minutes=20):
            current["last_at"] = at
            current["n_rescored"] += 1
        else:
            if current:
                setups.append(current)
            current = {"key": key, "last_at": at, "representative": r, "n_rescored": 1, "first_at": at}
        current["representative"] = current.get("representative") or r
    if current:
        setups.append(current)
    # keep the FIRST occurrence as representative (no look-ahead into a later, possibly better rescoring)
    result = []
    rows_sorted2 = sorted(rows_list, key=lambda r: (r["symbol"], r["at"]))
    cur = None
    for r in rows_sorted2:
        at = datetime.fromisoformat(r["at"])
        key = (r["symbol"], r["direction"])
        if cur and cur["key"] == key and (at - cur["last_at"]) <= timedelta(minutes=20):
            cur["last_at"] = at
        else:
            if cur:
                result.append(cur)
            cur = {"key": key, "last_at": at, "representative": r}
    if cur:
        result.append(cur)
    return [c["representative"] for c in result]


def stats_block(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    equity, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return {
        "n": n, "wr": round(len(wins) / n, 3), "exp": round(st.fmean(vals), 4),
        "pf": round(gw / gl, 3) if gl else None, "total_R": round(sum(vals), 3), "max_dd": round(dd, 3),
        "avg_winner": round(st.fmean(wins), 3) if wins else None, "avg_loser": round(st.fmean(losses), 3) if losses else None,
    }


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks
    rx, ry = rank(xs), rank(ys)
    mean_rx, mean_ry = st.fmean(rx), st.fmean(ry)
    cov = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    var_x = sum((a - mean_rx) ** 2 for a in rx)
    var_y = sum((b - mean_ry) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def comp(row, name):
    for c in row.get("components", []):
        if c.get("name") == name:
            return c
    return None


def part1_threshold_sweep(setups, span_days):
    print(f"\n{'='*110}\nPART 1 -- threshold sweep (unique setups={len(setups)}, span={span_days:.1f}d)\n{'='*110}")
    for thr in THRESHOLDS:
        if thr is None:
            filtered = setups
            label = "ALL (no confidence filter)"
        else:
            filtered = [r for r in setups if (r.get("overall_confidence") or 0) >= thr]
            label = f">= {thr}"
        gross_vals = [outcome_r(r, use_net=False) for r in filtered]
        net_vals_available = [r.get("net_outcome_r") for r in filtered if r.get("net_outcome_r") is not None]
        s = stats_block(gross_vals)
        trades_per_day = round(len(filtered) / span_days, 3) if span_days else None
        cost_note = f"cost_adj_n={len(net_vals_available)}" + (f" cost_adj_exp={round(st.fmean(net_vals_available),4)}" if net_vals_available else " (no cost data resolved -- HISTORICAL_ESTIMATE spread index empty for this window, reporting gross only)")
        print(f"  {label:<24} {s} trades/day={trades_per_day} | {cost_note}")


def part2_band_analysis(setups):
    print(f"\n{'='*110}\nPART 2 -- confidence band analysis + rank correlation\n{'='*110}")
    grouped = defaultdict(list)
    for r in setups:
        c = r.get("overall_confidence")
        if c is None:
            continue
        for lo, hi, name in BANDS:
            if lo <= c < hi:
                grouped[name].append(r)
                break
    for _, _, name in BANDS:
        rows = grouped.get(name, [])
        vals = [outcome_r(r, use_net=False) for r in rows]
        print(f"  {name:<10} {stats_block(vals)}")

    with_conf = [r for r in setups if r.get("overall_confidence") is not None and outcome_r(r, use_net=False) is not None]
    xs = [r["overall_confidence"] for r in with_conf]
    ys = [outcome_r(r, use_net=False) for r in with_conf]
    rho = spearman(xs, ys)
    print(f"\n  Spearman(confidence, outcome_r): n={len(with_conf)} rho={rho}")
    if rho is None:
        verdict = "insufficient data"
    elif abs(rho) < 0.05:
        verdict = "confidence shows NO meaningful ranking relationship with outcome (rho~0)"
    elif rho > 0.15:
        verdict = f"confidence is a genuine positive ranking function (rho={rho:.3f})"
    elif rho > 0:
        verdict = f"confidence is WEAKLY positively related to outcome (rho={rho:.3f}) -- not strongly monotonic"
    else:
        verdict = f"confidence is INVERTED/negatively related to outcome (rho={rho:.3f})"
    print(f"  VERDICT: {verdict}")

    # monotonicity check across bands
    band_exps = []
    for _, _, name in BANDS:
        rows = grouped.get(name, [])
        vals = [outcome_r(r, use_net=False) for r in rows]
        vals = [v for v in vals if v is not None]
        if vals:
            band_exps.append((name, st.fmean(vals)))
    is_monotonic = all(band_exps[i][1] <= band_exps[i + 1][1] for i in range(len(band_exps) - 1)) if len(band_exps) > 1 else None
    print(f"  band-expectancy sequence: {band_exps}")
    print(f"  strictly monotonic increasing across bands: {is_monotonic}")


def part3_65_70_hypothesis(setups):
    print(f"\n{'='*110}\nPART 3 -- 65-70 vs 70-75 hypothesis test\n{'='*110}")
    b_65_70 = [r for r in setups if 65 <= (r.get("overall_confidence") or -1) < 70]
    b_70_75 = [r for r in setups if 70 <= (r.get("overall_confidence") or -1) < 75]
    vals_65_70 = [outcome_r(r, use_net=False) for r in b_65_70]
    vals_70_75 = [outcome_r(r, use_net=False) for r in b_70_75]
    print(f"  65-70: {stats_block(vals_65_70)}")
    print(f"  70-75: {stats_block(vals_70_75)}")
    if vals_65_70 and vals_70_75:
        exp_65_70 = st.fmean([v for v in vals_65_70 if v is not None])
        exp_70_75 = st.fmean([v for v in vals_70_75 if v is not None])
        print(f"  65-70 outperforms 70-75: {exp_65_70 > exp_70_75} (delta={exp_65_70 - exp_70_75:+.4f})")
        print(f"  65-70 itself net POSITIVE: {exp_65_70 > 0}")

    print("\n  -- chronological split (first half vs second half of 65-70 population) --")
    b_65_70_sorted = sorted(b_65_70, key=lambda r: r["at"])
    mid = len(b_65_70_sorted) // 2
    if mid > 0:
        print(f"    first half:  {stats_block([outcome_r(r, use_net=False) for r in b_65_70_sorted[:mid]])}")
        print(f"    second half: {stats_block([outcome_r(r, use_net=False) for r in b_65_70_sorted[mid:]])}")
    else:
        print("    too few rows to split")

    print("\n  -- per-symbol split of 65-70 population --")
    by_symbol = defaultdict(list)
    for r in b_65_70:
        by_symbol[r["symbol"]].append(outcome_r(r, use_net=False))
    for sym, vals in by_symbol.items():
        print(f"    {sym}: {stats_block(vals)}")


def part4_rr_quality_ab(setups):
    print(f"\n{'='*110}\nPART 4 -- RR-quality A/B\n{'='*110}")
    print("  REAL FINDING: the reconstruction container has MT5_MTFAI1_V2_RR_QUALITY_ENABLED=false")
    print("  (left off deliberately, per instruction not to enable pending evidence) -- so")
    print("  _mtfai1_v2_reward_risk_quality() returned None for every row, and EVERY persisted row's")
    print("  reward_risk_quality component is ALREADY scenario A (the current/global formula).")
    print("  Scenario B (V2-aware) is NOT fully reconstructable from already-persisted fields alone:")
    print("  atr_distance_score and reachability_score need raw atr/opposing_structure, which were")
    print("  not persisted per-row. This computes a PARTIAL scenario B using the two sub-components")
    print("  that ARE reconstructable from persisted fields (risk_reward, tp_basis) -- structural-")
    print("  destination(35%) + abs-RR(25%), reweighted to 100% of a 2-component approximation.")
    print("  This is NOT the full 4-component V2-aware score -- flagged honestly, not fabricated.")

    _STRUCT_W, _ABS_RR_W = 0.35 / 0.60, 0.25 / 0.60  # renormalized to the 2 reconstructable sub-components
    _RR_FLOOR_V2, _RR_TARGET_V2 = 1.0, 2.5

    def generic_rr_score(rr):
        if rr is None or rr <= 0:
            return 0.0
        if rr <= _REWARD_RISK_FLOOR:
            return 50.0 * (rr / _REWARD_RISK_FLOOR)
        span = _REWARD_RISK_TARGET - _REWARD_RISK_FLOOR
        return 50.0 + 50.0 * min(1.0, (rr - _REWARD_RISK_FLOOR) / span)

    def v2_partial_rr_score(rr, tp_basis):
        if rr is None or rr <= 0:
            return None
        structural = 100.0 if str(tp_basis).startswith("mtfai1_v2_") else 40.0
        if rr <= _RR_FLOOR_V2:
            abs_rr_score = 50.0 * (rr / _RR_FLOOR_V2)
        else:
            span = _RR_TARGET_V2 - _RR_FLOOR_V2
            abs_rr_score = 50.0 + 50.0 * min(1.0, (rr - _RR_FLOOR_V2) / span)
        return structural * _STRUCT_W + abs_rr_score * _ABS_RR_W

    recomputed = []
    for r in setups:
        rr_comp = comp(r, "reward_risk_quality")
        if not rr_comp or r.get("overall_confidence") is None:
            continue
        rr_value = None
        try:
            rr_value = float(r.get("risk_reward"))
        except (TypeError, ValueError):
            pass
        if rr_value is None:
            continue
        weight = rr_comp.get("weight") or 0.14
        a_score = rr_comp["score"]  # confirmed: this IS the generic/global formula's output (flag was off)
        b_score = v2_partial_rr_score(rr_value, r.get("tp_basis"))
        if b_score is None:
            continue
        b_overall = max(0.0, min(100.0, r["overall_confidence"] + (b_score - a_score) * weight))
        recomputed.append({"row": r, "a_overall": r["overall_confidence"], "b_overall": b_overall, "a_score": a_score, "b_score": b_score, "outcome": outcome_r(r, use_net=False)})

    valid = [x for x in recomputed if x["outcome"] is not None]
    print(f"\n  n={len(valid)} setups with usable RR + outcome data")
    for label, key in (("A (current/global RR)", "a_overall"), ("B (V2-aware RR)", "b_overall")):
        xs = [x[key] for x in valid]
        ys = [x["outcome"] for x in valid]
        rho = spearman(xs, ys)
        print(f"  {label}: Spearman(confidence, outcome)={rho}")
        for thr in (70, 75):
            passed = [x["outcome"] for x in valid if x[key] >= thr]
            print(f"    @>={thr}: {stats_block(passed)}")

    # winner/loser separation: mean RR-component score for winners vs losers, under each scenario
    winners = [x for x in valid if x["outcome"] > 0]
    losers = [x for x in valid if x["outcome"] < 0]
    if winners and losers:
        print(f"\n  winner/loser separation (mean RR-component score):")
        print(f"    A (global):  winners={st.fmean(x['a_score'] for x in winners):.2f} losers={st.fmean(x['a_score'] for x in losers):.2f} gap={st.fmean(x['a_score'] for x in winners)-st.fmean(x['a_score'] for x in losers):+.2f}")
        print(f"    B (V2-aware): winners={st.fmean(x['b_score'] for x in winners):.2f} losers={st.fmean(x['b_score'] for x in losers):.2f} gap={st.fmean(x['b_score'] for x in winners)-st.fmean(x['b_score'] for x in losers):+.2f}")


def part5_component_attribution(setups):
    print(f"\n{'='*110}\nPART 5 -- component attribution\n{'='*110}")
    names = ["trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "volatility_suitability",
             "execution_conditions", "signal_freshness", "strategy_performance", "symbol_performance", "correlation_quality"]
    for name in names:
        pairs = []
        for r in setups:
            c = comp(r, name)
            o = outcome_r(r, use_net=False)
            if c is not None and o is not None:
                pairs.append((c["score"], o))
        if len(pairs) < 20:
            print(f"  {name:<24} INSUFFICIENT_DATA (n={len(pairs)})")
            continue
        scores = [s for s, o in pairs]
        if len(set(scores)) <= 1:
            print(f"  {name:<24} INSUFFICIENT_DATA (constant, no variance)")
            continue
        rho = spearman(scores, [o for s, o in pairs])
        pairs_sorted = sorted(pairs, key=lambda x: x[0])
        q = max(1, len(pairs_sorted) // 4)
        quantile_exp = []
        for i in range(4):
            chunk = pairs_sorted[i * q:(i + 1) * q] if i < 3 else pairs_sorted[i * q:]
            if chunk:
                quantile_exp.append(round(st.fmean([o for s, o in chunk]), 3))
        monotonic_up = all(quantile_exp[i] <= quantile_exp[i + 1] for i in range(len(quantile_exp) - 1))
        if rho is None:
            classification = "INSUFFICIENT_DATA"
        elif rho > 0.15:
            classification = "GOOD"
        elif rho > 0.05:
            classification = "WEAK"
        elif rho >= -0.05:
            classification = "NON_MONOTONIC" if not monotonic_up else "WEAK"
        else:
            classification = "INVERTED"
        print(f"  {name:<24} n={len(pairs):<5} rho={round(rho,3) if rho is not None else None} quantiles(Q1-Q4)={quantile_exp} monotonic_up={monotonic_up} -> {classification}")
    print(f"\n  Historical Intelligence: not scored in this reconstruction (matches the live V2-neutral gate exactly -- always NEUTRAL/0 adjustment, no variance to test, by design not a limitation).")


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} raw rows from {INPUT_PATH}")
    if not rows:
        print("No data yet.")
        return
    res = resolved(rows)
    setups = unique_setups(res)
    dates = [datetime.fromisoformat(r["at"]) for r in setups]
    span_days = max((max(dates) - min(dates)).total_seconds() / 86400.0, 0.1) if dates else 0.1
    print(f"resolved rows: {len(res)} | unique setups: {len(setups)} | span: {span_days:.1f} days")
    symbols_covered = Counter(r["symbol"] for r in setups)
    print(f"symbols covered so far: {dict(symbols_covered)}")
    if len(setups) < 100:
        print("\n*** SAMPLE STILL SMALL -- treat all results below as PRELIMINARY, re-run once reconstruction completes ***\n")

    part1_threshold_sweep(setups, span_days)
    part2_band_analysis(setups)
    part3_65_70_hypothesis(setups)
    part4_rr_quality_ab(setups)
    part5_component_attribution(setups)


if __name__ == "__main__":
    main()
