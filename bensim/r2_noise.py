"""R2: strategy outcomes vs the Time Machine noise baseline (docs/audit/r1_r2_preregistration_2026-11-03.md).

    python -m bensim.r2_noise --split validation        # then, once, after the validation report is frozen:
    python -m bensim.r2_noise --split confirmation
Per trade: bensim-mode null (26-week hour-of-week blocks before the entry DAY, drift removed), 2,000 replays, seed 19,
the trade's own symbol / direction / entry / stop / target / start / horizon, observed_account spread model.
Horizon = time of the 600th M5 bar after entry (200 M15 bars), fib618 also Friday 20:00 UTC (live.py).
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import registry, timemachine, validate
from .backtest import exec_profile, friday_close_ts, load_m5

DIR = Path("reports/experiments/r12_2026-11-03")
STRATS = ["fib618", "enigma", "imbalance"]
FRIDAY = {"fib618"}
N, SEED, WEEKS = 2000, 19, 26
HOLD_M5 = 600


def null_for_symbol(a) -> pd.DataFrame:
    sym, g = a
    ex = exec_profile("observed_account")
    lo = pd.to_datetime(g.t_entry.min() - (WEEKS * 7 + 2) * 86400, unit="s", utc=True).isoformat()
    hi = pd.to_datetime(g.t_entry.max() + 30 * 86400, unit="s", utc=True).isoformat()
    m5 = load_m5(sym, lo, hi)
    t = m5["t"]
    cache: dict[int, dict] = {}
    rows = []
    for i, x in g.iterrows():
        day = int(x.t_entry) // 86400 * 86400
        if day not in cache:  # pool = 26 weeks strictly before the entry day (no lookahead)
            a_, b_ = np.searchsorted(t, day - WEEKS * 7 * 86400), np.searchsorted(t, day)
            cache = {day: timemachine.blocks({k: v[a_:b_] for k, v in m5.items()})}
        pool = cache[day]
        k = int(np.searchsorted(t, x.t_entry - 300))
        until = int(t[min(k + HOLD_M5, len(t) - 1)]) + 300
        if x.strategy in FRIDAY:
            until = min(until, friday_close_ts(int(x.t_entry), 20))
        r = timemachine.replay(pool, sym, int(x.dir), x.entry, x.sl, x.tp, x.entry, int(x.t_entry), until, n=N, seed=SEED, ex=ex)
        rows.append({"i": i, "tm_target": r["target"], "tm_stop": r["stop"], "tm_open": r["open"], "tm_avg_r": r["avg_r"]})
    return pd.DataFrame(rows).set_index("i")


def week_boot_mean(t: pd.DataFrame, col: str, paths: int = 10_000, seed: int = SEED) -> dict:
    wk = (t.t_entry // (7 * 86400)).to_numpy()
    uw, inv = np.unique(wk, return_inverse=True)
    s, n = np.bincount(inv, t[col].to_numpy(), len(uw)), np.bincount(inv, None, len(uw))
    idx = np.random.default_rng(seed).integers(0, len(uw), (paths, len(uw)))
    m = s[idx].sum(1) / n[idx].sum(1)
    return {"mean": round(float(t[col].mean()), 4), "ci95": [round(float(np.quantile(m, q)), 4) for q in (0.025, 0.975)],
            "p_one_sided_gt0": round(float((m <= 0).mean()), 4), "weeks": int(len(uw))}


def auc(score: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = score[y == 1], score[y == 0]
    if not len(pos) or not len(neg):
        return None
    s = np.r_[pos, neg]
    rk = pd.Series(s).rank().to_numpy()
    return round(float((rk[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 3)


def analyse(t: pd.DataFrame) -> dict:
    hit = (t.exit_reason == "target").astype(float)
    t = t.assign(hit=hit, excess_hit=hit - t.tm_target, excess_r=t.r_gross - t.tm_avg_r,
                 excess_r_net=t.r - (t.tm_avg_r - t.commission_r + t.swap_r.fillna(0)))
    q = pd.qcut(t.tm_target.rank(method="first"), 4, labels=["Q1 low", "Q2", "Q3", "Q4 high"])
    cal = t.groupby(q, observed=True).agg(n=("hit", "size"), tm_p_target=("tm_target", "mean"), real_hit=("hit", "mean"),
                                          r=("r", "mean")).round(3)
    yr = pd.to_datetime(t.t_entry, unit="s").dt.year
    win = (t.r > 0).astype(int).to_numpy()
    return {"trades": int(len(t)), "real_hit_rate": round(float(hit.mean()), 3), "tm_p_target": round(float(t.tm_target.mean()), 3),
            "real_r_gross": round(float(t.r_gross.mean()), 3), "tm_avg_r": round(float(t.tm_avg_r.mean()), 3), "real_r_net": round(float(t.r.mean()), 3),
            "excess_hit": week_boot_mean(t, "excess_hit"), "excess_r": week_boot_mean(t, "excess_r"), "excess_r_net": week_boot_mean(t, "excess_r_net"),
            "calibration": cal.reset_index().astype({"tm_target": str}).to_dict("records"),
            "auc_win_tm_target": auc(t.tm_target.to_numpy(), win), "auc_win_geometry_1_over_1_plus_rr": auc((1 / (1 + t.rr_planned)).to_numpy(), win),
            "by_year": {str(y): {"n": int(len(g)), "excess_hit": round(float(g.excess_hit.mean()), 3), "excess_r": round(float(g.excess_r.mean()), 3)}
                        for y, g in t.groupby(yr)},
            "by_pair": {s: {"n": int(len(g)), "excess_hit": round(float(g.excess_hit.mean()), 3), "excess_r": round(float(g.excess_r.mean()), 3)}
                        for s, g in t.groupby("symbol")}}


def md(split: str, res: dict, holm: dict) -> str:
    L = [f"# R2 strategy vs Time Machine noise: {split}", "",
         f"Matched null per trade (same symbol/direction/entry/stop/target/start/horizon/spread), {N:,} replays, seed {SEED}, "
         "26-week hour-of-week pool before the entry day, drift removed. Week-block bootstrap 10,000. Holm over 6 tests.", ""]
    for s, r in res.items():
        L += [f"## {s} ({r['trades']} trades)",
              f"- target hit: real {r['real_hit_rate']:.3f} vs noise {r['tm_p_target']:.3f}; R gross: real {r['real_r_gross']:+.3f} vs noise {r['tm_avg_r']:+.3f}; net real {r['real_r_net']:+.3f}",
              f"- **excess hit {r['excess_hit']['mean']:+.4f}, 95% CI {r['excess_hit']['ci95']}, p {r['excess_hit']['p_one_sided_gt0']}, Holm {holm.get(s + '|hit')}**",
              f"- **excess R (gross) {r['excess_r']['mean']:+.4f}, 95% CI {r['excess_r']['ci95']}, p {r['excess_r']['p_one_sided_gt0']}, Holm {holm.get(s + '|r')}**",
              f"- excess R net (after commission+swap) {r['excess_r_net']['mean']:+.4f}, CI {r['excess_r_net']['ci95']}",
              f"- AUC(win): TM P(target) {r['auc_win_tm_target']} vs geometry alone {r['auc_win_geometry_1_over_1_plus_rr']}",
              "- calibration (TM P(target) quartile -> real hit): " + "; ".join(f"{c['tm_target']}: n{c['n']} tm {c['tm_p_target']} real {c['real_hit']}" for c in r["calibration"]),
              "- by year: " + "; ".join(f"{y} n{v['n']} hit {v['excess_hit']:+.3f} R {v['excess_r']:+.3f}" for y, v in r["by_year"].items()),
              "- by pair: " + "; ".join(f"{p} n{v['n']} hit {v['excess_hit']:+.3f} R {v['excess_r']:+.3f}" for p, v in r["by_pair"].items()), ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["validation", "confirmation"], required=True)
    a = ap.parse_args()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    t = pd.read_csv(DIR / f"trades_{a.split}.csv")
    t = t[t.strategy.isin(STRATS)].reset_index(drop=True)
    with ProcessPoolExecutor(3) as pool:
        nulls = pd.concat(list(pool.map(null_for_symbol, list(t.groupby("symbol")))))
    t = t.join(nulls)
    t.to_csv(DIR / f"r2_trades_with_null_{a.split}.csv", index=False)
    res = {s: analyse(t[t.strategy == s]) for s in STRATS}
    holm = validate.holm({f"{s}|{m}": res[s][k]["p_one_sided_gt0"] for s in STRATS for m, k in (("hit", "excess_hit"), ("r", "excess_r"))})
    (DIR / f"R2_{a.split}.json").write_text(json.dumps({"results": res, "holm": holm}, indent=1, default=str))
    (DIR / f"R2_{a.split}.md").write_text(md(a.split, res, holm), encoding="utf-8")
    registry.append({"kind": f"R2 strategy vs noise ({a.split})", "strategy": STRATS, "code": registry.code_version(),
                     "command": f"python -m bensim.r2_noise --split {a.split}", "outputs": {"report": str(DIR / f"R2_{a.split}.md")},
                     "summary": {s: {"trades": r["trades"], "excess_hit": r["excess_hit"], "excess_r": r["excess_r"]} for s, r in res.items()} | {"holm": holm}})
    print(md(a.split, res, holm))


if __name__ == "__main__":
    main()
