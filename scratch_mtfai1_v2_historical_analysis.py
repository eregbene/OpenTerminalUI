"""MTFAI1 V2 deep confidence audit -- Parts 4-8. Reads the JSONL produced by
scratch_mtfai1_v2_historical_reconstruction.py (point-in-time-safe, no look-ahead in performance
memory) and answers: does confidence actually predict outcome, which components are predictive/
inverted/redundant, and does the confidence gate improve, do nothing to, or destroy MTFAI1 V2's
underlying strategy edge.

Run any time against a partial file -- reports real N throughout, never fabricates.
"""
import json
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta

INPUT_PATH = "/data/historical_intelligence/mtfai1_v2_reconstruction.jsonl"

BANDS = [(0, 50, "<50"), (50, 55, "50-55"), (55, 60, "55-60"), (60, 65, "60-65"), (65, 70, "65-70"),
         (70, 72.5, "70-72.5"), (72.5, 75, "72.5-75"), (75, 77.5, "75-77.5"), (77.5, 80, "77.5-80"),
         (80, 85, "80-85"), (85, 101, "85+")]

COMPONENTS = ["trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "volatility_suitability",
              "execution_conditions", "signal_freshness", "strategy_performance", "symbol_performance", "correlation_quality"]


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


def outcome_r(row):
    return row.get("net_outcome_r") if row.get("net_outcome_r") is not None else row.get("outcome_r")


def resolved(rows):
    return [r for r in rows if r.get("resolution_status") == "RESOLVED" and outcome_r(r) is not None]


def stats(vals, mfes=None, maes=None):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    out = {"n": n, "wr": round(len(wins) / n, 3), "exp": round(st.fmean(vals), 4), "pf": round(gw / gl, 3) if gl else None}
    equity, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    out["max_dd"] = round(dd, 3)
    if mfes:
        m = [x for x in mfes if x is not None]
        if m:
            out["avg_mfe"] = round(st.fmean(m), 3)
    if maes:
        a = [x for x in maes if x is not None]
        if a:
            out["avg_mae"] = round(st.fmean(a), 3)
    return out


def unique_setups(rows_list):
    """Dedup consecutive same symbol+direction (<=20min gap) into one setup, representative =
    FIRST occurrence (the setup's initial decision point -- avoids any look-ahead bias from
    picking a LATER, possibly more favorable rescoring)."""
    rows_sorted = sorted(rows_list, key=lambda r: (r["symbol"], r["at"]))
    setups = []
    current = None
    for r in rows_sorted:
        at = datetime.fromisoformat(r["at"])
        key = (r["symbol"], r["direction"])
        if current and current["key"] == key and (at - current["last_at"]) <= timedelta(minutes=20):
            current["last_at"] = at
            current["n_rescored"] += 1
        else:
            if current:
                setups.append(current["representative"])
            current = {"key": key, "last_at": at, "representative": r, "n_rescored": 1}
    if current:
        setups.append(current["representative"])
    return setups


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


def part4_band_table(setups):
    print(f"\n{'='*100}\nPART 4 -- confidence-band outcome table, UNIQUE SETUPS (n={len(setups)})\n{'='*100}")
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
        if not rows:
            print(f"  {name:<10} n=0")
            continue
        vals = [outcome_r(r) for r in rows]
        gross_vals = [r.get("outcome_r") for r in rows]
        net_vals = [r.get("net_outcome_r") for r in rows if r.get("net_outcome_r") is not None]
        s = stats(vals, [r.get("mfe_r") for r in rows], [r.get("mae_r") for r in rows])
        symbols = Counter(r["symbol"] for r in rows)
        regimes = Counter(r["regime"] for r in rows)
        directions = Counter(r["direction"] for r in rows)
        cost_adj = f"n_with_cost_data={len(net_vals)} avg_net={round(st.fmean(net_vals),4) if net_vals else 'N/A'}"
        print(f"  {name:<10} {s} | symbols={dict(symbols)} | regime={dict(regimes)} | dir={dict(directions)} | cost_adj: {cost_adj}")

    print("\n--- Spearman correlation (confidence vs outcome, confidence vs MFE) ---")
    with_conf = [r for r in setups if r.get("overall_confidence") is not None]
    xs = [r["overall_confidence"] for r in with_conf]
    ys_outcome = [outcome_r(r) for r in with_conf]
    ys_mfe = [r.get("mfe_r") for r in with_conf]
    valid_outcome = [(x, y) for x, y in zip(xs, ys_outcome) if y is not None]
    valid_mfe = [(x, y) for x, y in zip(xs, ys_mfe) if y is not None]
    rho_outcome = spearman([x for x, y in valid_outcome], [y for x, y in valid_outcome])
    rho_mfe = spearman([x for x, y in valid_mfe], [y for x, y in valid_mfe])
    print(f"  Spearman(confidence, outcome_r): n={len(valid_outcome)} rho={rho_outcome}")
    print(f"  Spearman(confidence, mfe_r): n={len(valid_mfe)} rho={rho_mfe}")
    if rho_outcome is not None:
        if abs(rho_outcome) < 0.05:
            verdict = "confidence shows essentially NO ranking relationship with outcome"
        elif rho_outcome > 0:
            verdict = f"confidence is a WEAK-TO-MODERATE positive ranking function (rho={rho_outcome:.3f})"
        else:
            verdict = f"confidence is INVERTED relative to outcome (rho={rho_outcome:.3f})"
        print(f"  VERDICT: {verdict}")


def part5_component_attribution(setups):
    print(f"\n{'='*100}\nPART 5 -- component attribution (n={len(setups)} unique setups)\n{'='*100}")
    for name in COMPONENTS:
        pairs = []
        for r in setups:
            c = comp(r, name)
            o = outcome_r(r)
            if c is not None and o is not None:
                pairs.append((c["score"], o))
        if len(pairs) < 10:
            print(f"  {name:<24} INSUFFICIENT_DATA (n={len(pairs)})")
            continue
        scores = [s for s, o in pairs]
        if len(set(scores)) <= 1:
            print(f"  {name:<24} INSUFFICIENT_DATA (constant score, no variance -- known simplification for this component)")
            continue
        rho = spearman(scores, [o for s, o in pairs])
        # quantile bucket check for monotonicity
        pairs_sorted = sorted(pairs, key=lambda x: x[0])
        q = max(1, len(pairs_sorted) // 4)
        quantile_exp = []
        for i in range(4):
            chunk = pairs_sorted[i * q:(i + 1) * q] if i < 3 else pairs_sorted[i * q:]
            if chunk:
                quantile_exp.append(round(st.fmean([o for s, o in chunk]), 3))
        if rho is None:
            classification = "INSUFFICIENT_DATA"
        elif abs(rho) < 0.05:
            classification = "NEUTRAL"
        elif rho > 0:
            classification = "PREDICTIVE"
        else:
            classification = "INVERTED"
        print(f"  {name:<24} n={len(pairs):<5} rho={rho} quantile_exp(Q1->Q4)={quantile_exp} -> {classification}")

    print("\n--- redundancy check: pairwise correlation between component scores ---")
    for i, name_a in enumerate(COMPONENTS):
        for name_b in COMPONENTS[i + 1:]:
            pairs = []
            for r in setups:
                ca, cb = comp(r, name_a), comp(r, name_b)
                if ca is not None and cb is not None:
                    pairs.append((ca["score"], cb["score"]))
            if len(pairs) < 10:
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            if len(set(xs)) <= 1 or len(set(ys)) <= 1:
                continue
            rho = spearman(xs, ys)
            if rho is not None and abs(rho) > 0.5:
                print(f"  POSSIBLE REDUNDANCY: {name_a} vs {name_b}: rho={rho:.3f} (n={len(pairs)})")


def part6_edge_vs_gate(setups):
    print(f"\n{'='*100}\nPART 6 -- strategy edge vs confidence gate (n={len(setups)} unique setups)\n{'='*100}")
    dates = [datetime.fromisoformat(r["at"]) for r in setups]
    span_days = max((max(dates) - min(dates)).days, 1) if dates else 1
    configs = [
        ("A: all V2-valid setups (no confidence filter)", lambda r: True),
        ("B: same as A (symbol/regime restriction already baked into generation)", lambda r: True),
        ("C: B + confidence >= 60", lambda r: (r.get("overall_confidence") or 0) >= 60),
        ("D: B + confidence >= 65", lambda r: (r.get("overall_confidence") or 0) >= 65),
        ("E: B + confidence >= 70", lambda r: (r.get("overall_confidence") or 0) >= 70),
        ("F: B + confidence >= 72.5", lambda r: (r.get("overall_confidence") or 0) >= 72.5),
        ("G: B + confidence >= 75", lambda r: (r.get("overall_confidence") or 0) >= 75),
        ("H: B + confidence >= 80", lambda r: (r.get("overall_confidence") or 0) >= 80),
    ]
    baseline_exp = None
    for label, pred in configs:
        filtered = [r for r in setups if pred(r)]
        vals = [outcome_r(r) for r in filtered]
        net_vals = [r.get("net_outcome_r") for r in filtered if r.get("net_outcome_r") is not None]
        s = stats(vals)
        trades_per_day = round(len(filtered) / span_days, 2)
        print(f"  {label:<55} {s} trades/day={trades_per_day} (cost-adj n={len(net_vals)})")
        if label.startswith("A"):
            baseline_exp = s.get("exp")
    print(f"\n  (span: {span_days} days)")
    return baseline_exp


def part8_chronological_stability(setups):
    print(f"\n{'='*100}\nPART 8 -- chronological stability (first half vs second half)\n{'='*100}")
    setups_sorted = sorted(setups, key=lambda r: r["at"])
    mid = len(setups_sorted) // 2
    first_half, second_half = setups_sorted[:mid], setups_sorted[mid:]
    for label, half in (("first half (earlier)", first_half), ("second half (later)", second_half)):
        for threshold in (0, 75):
            filtered = [r for r in half if (r.get("overall_confidence") or 0) >= threshold]
            vals = [outcome_r(r) for r in filtered]
            print(f"  {label} @conf>={threshold}: {stats(vals)}")
    # per-symbol stability at the >=75 gate
    print("\n  per-symbol stability @ confidence>=75:")
    by_symbol = defaultdict(list)
    for r in setups_sorted:
        if (r.get("overall_confidence") or 0) >= 75:
            by_symbol[r["symbol"]].append(outcome_r(r))
    for sym, vals in by_symbol.items():
        print(f"    {sym}: {stats(vals)}")


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} raw candidate rows from {INPUT_PATH}")
    if not rows:
        print("No data yet -- reconstruction still running or hasn't started. Re-run later.")
        return
    res = resolved(rows)
    print(f"resolved rows: {len(res)}")
    setups = unique_setups(res)
    print(f"unique setups (20min-gap dedup): {len(setups)}")
    if len(setups) < 30:
        print("\nToo early for a full breakdown (n<30 unique setups) -- showing what's available, re-run as more data accumulates.\n")

    part4_band_table(setups)
    part5_component_attribution(setups)
    part6_edge_vs_gate(setups)
    part8_chronological_stability(setups)


if __name__ == "__main__":
    main()
