"""Independent validation engine (audit P1, 2026-11-03). Reads trade CSVs only; never trades, never tunes.

    python -m bensim.validate trades.csv [--name X] [--trials N]     # -> markdown + json next to the CSV

Per trade set:
  * expectancy (net R), gross R (after spread/slippage, before commission+swap), commission R, swap R, win rate, PF
  * Sharpe / Sortino on DAILY R (every weekday in the period, flat days = 0; x sqrt(252)), and per-trade Sharpe
  * PSR(0) and DSR (Bailey & Lopez de Prado 2012/2014) on the per-trade Sharpe; DSR's N and trial variance come
    from the experiment registry (bensim.registry) -- every run ever made counts as a trial
  * effective sample size from lag-1 autocorrelation of daily R; day-clustered t
  * circular block bootstrap of daily R (block ~ n^(1/3) days, 5,000 paths, seed 19): CI of total R and max DD,
    P(total R <= 0)
  * drawdown on cumulative R: max DD, longest time under water, time to recover from the max DD (or "not recovered")
  * splits: by pair, by session (entry hour UTC), by causal D1 regime at entry (trend/range by 20-day efficiency
    ratio >= 0.30, high/low vol by D1 ATR14 above/below its own 250-day median -- both from bars BEFORE the entry
    day), by month (frozen-parameter walk-forward: no refitting, every month is out-of-sample for fixed rules)
Across trade sets: PBO by CSCV (Bailey et al. 2015) over the strategies' daily R, Holm-adjusted p-values.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

EULER = 0.5772156649
SESSIONS = [(0, 7, "Asia 00-07"), (7, 12, "London 07-12"), (12, 16, "London/NY 12-16"), (16, 21, "NY 16-21"), (21, 24, "rollover 21-24")]


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p: float) -> float:  # Acklam-free: bisection is plenty here
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _phi(mid) < p else (lo, mid)
    return (lo + hi) / 2


def psr(sr: float, n: int, skew: float, kurt: float, sr_star: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > sr_star) for a per-period SR from n observations (kurt = raw, normal 3)."""
    if n < 3:
        return float("nan")
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    return _phi((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(max(den, 1e-12)))


def dsr_threshold(trial_srs: list[float], n_trials: int) -> float:
    """Expected max Sharpe of n_trials unskilled trials with the observed trial variance (the DSR's SR*)."""
    if n_trials < 2 or len(trial_srs) < 2:
        return 0.0
    v = float(np.var(trial_srs, ddof=1))
    return math.sqrt(v) * ((1 - EULER) * _phi_inv(1 - 1 / n_trials) + EULER * _phi_inv(1 - 1 / (n_trials * math.e)))


def daily_series(t: pd.DataFrame, start: str | None = None, end: str | None = None) -> pd.Series:
    d = pd.to_datetime(t.t_exit, unit="s", utc=True).dt.normalize()
    s = t.r.groupby(d).sum()
    lo = pd.Timestamp(start, tz="UTC") if start else s.index.min()
    hi = pd.Timestamp(end, tz="UTC") - pd.Timedelta(days=1) if end else s.index.max()
    days = pd.bdate_range(lo.normalize(), hi.normalize(), tz="UTC")
    return s.reindex(days.union(s.index), fill_value=0.0).sort_index()


def drawdown(r: np.ndarray, t_exit: np.ndarray) -> dict:
    if not len(r):
        return {}
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    dd = peak - eq
    i = int(dd.argmax())
    pk = int(np.flatnonzero(np.r_[0.0, eq][: i + 2] == peak[i])[0]) - 1  # trade index of the peak (-1 = start)
    rec = np.flatnonzero(eq[i:] >= peak[i])
    t_pk = t_exit[pk] if pk >= 0 else t_exit[0]
    under, longest, t0 = False, 0.0, None
    for k in range(len(eq)):
        if dd[k] > 1e-12 and not under:
            under, t0 = True, t_exit[k - 1] if k else t_exit[0]
        elif dd[k] <= 1e-12 and under:
            under, longest = False, max(longest, (t_exit[k] - t0) / 86400)
    if under:
        longest = max(longest, (t_exit[-1] - t0) / 86400)
    return {"max_dd_r": round(float(dd.max()), 2), "max_dd_peak_to_trough_days": round(float((t_exit[i] - t_pk) / 86400), 1),
            "max_dd_recovery_days": round(float((t_exit[i + rec[0]] - t_exit[i]) / 86400), 1) if len(rec) else "not recovered",
            "longest_underwater_days": round(float(longest), 1)}


def block_bootstrap(daily: np.ndarray, paths: int = 5000, seed: int = 19) -> dict:
    n = len(daily)
    if n < 10:
        return {}
    b = max(2, int(round(n ** (1 / 3))))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, (paths, math.ceil(n / b)))
    idx = ((starts[:, :, None] + np.arange(b)) % n).reshape(paths, -1)[:, :n]
    x = daily[idx]
    tot = x.sum(1)
    eq = np.cumsum(x, 1)
    mdd = (np.maximum.accumulate(np.c_[np.zeros(paths), eq], 1)[:, 1:] - eq).max(1)
    return {"block_days": b, "paths": paths, "seed": seed, "total_r_ci90": [round(float(np.quantile(tot, q)), 1) for q in (0.05, 0.95)],
            "p_total_le_0": round(float((tot <= 0).mean()), 3), "max_dd_r_median": round(float(np.median(mdd)), 1),
            "max_dd_r_p95": round(float(np.quantile(mdd, 0.95)), 1)}


def regime_labels(t: pd.DataFrame) -> pd.DataFrame:
    """Causal D1 regime per trade from M5 bars of the days BEFORE the entry day."""
    from .backtest import load_m5, resample
    out = []
    for sym, g in t.groupby("symbol"):
        lo = pd.to_datetime(g.t_entry.min(), unit="s", utc=True) - pd.Timedelta(days=400)
        m5 = load_m5(sym, lo.isoformat(), pd.to_datetime(g.t_entry.max(), unit="s", utc=True).isoformat())
        d1 = pd.DataFrame(resample(m5, 86400)).set_index("t")
        c, h, l = d1.close, d1.high, d1.low
        er = (c - c.shift(20)).abs() / c.diff().abs().rolling(20).sum()
        atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(1).rolling(14).mean()
        vol_hi = atr > atr.rolling(250, min_periods=120).median()
        for i, x in g.iterrows():
            day = int(x.t_entry) // 86400 * 86400
            prev = d1.index[d1.index < day]
            if not len(prev) or pd.isna(er.get(prev[-1])):
                out.append((i, "unknown", "unknown"))
                continue
            k = prev[-1]
            out.append((i, "trend" if er[k] >= 0.30 else "range", "high vol" if bool(vol_hi[k]) else "low vol"))
    lab = pd.DataFrame(out, columns=["i", "trend_regime", "vol_regime"]).set_index("i")
    return t.join(lab)


def _session(h: int) -> str:
    return next(name for a, b, name in SESSIONS if a <= h < b)


def _group(t: pd.DataFrame, key) -> dict:
    return {str(k): {"n": int(len(g)), "avg_r": round(float(g.r.mean()), 3), "total_r": round(float(g.r.sum()), 1),
                     "win_rate": round(float((g.r > 0).mean()), 3)} for k, g in t.groupby(key)}


def report(t: pd.DataFrame, start: str | None = None, end: str | None = None, trials: int | None = None,
           trial_srs: list[float] | None = None, regimes: bool = True) -> dict:
    if t.empty:
        return {"trades": 0}
    t = t.sort_values("t_exit").reset_index(drop=True)
    r = t.r.to_numpy()
    n = len(r)
    sd = r.std(ddof=1) if n > 1 else float("nan")
    sr = float(r.mean() / sd) if sd > 0 else float("nan")
    sk = float(pd.Series(r).skew()) if n > 2 else 0.0
    ku = float(pd.Series(r).kurt()) + 3 if n > 3 else 3.0
    daily = daily_series(t, start, end)
    dv = daily.to_numpy()
    rho = float(pd.Series(dv).autocorr(1)) if len(dv) > 2 else 0.0
    ess = len(dv) * (1 - rho) / (1 + rho) if -1 < rho < 1 else float(len(dv))
    down = dv[dv < 0]
    if trials is None or trial_srs is None:
        from . import registry
        trial_srs = registry.trial_sharpes()
        trials = len(registry.records())
    sr_star = dsr_threshold(trial_srs, trials)
    gw, gl = r[r > 0].sum(), -r[r < 0].sum()
    out = {
        "trades": n, "win_rate": round(float((r > 0).mean()), 3), "expectancy_net_r": round(float(r.mean()), 3),
        "gross_r_before_commission_swap": round(float(t.r_gross.mean()), 3),
        "commission_r": round(float(t.commission_r.mean()), 3),
        "swap_r": round(float(t.swap_r.mean()), 3) if "swap_r" in t else None,
        "swap_unknown_trades": int((t.swap_known == False).sum()) if "swap_known" in t else None,  # noqa: E712
        "total_r": round(float(r.sum()), 1), "profit_factor": round(float(gw / gl), 2) if gl > 0 else None,
        "trade_t": round(float(r.mean() / sd * math.sqrt(n)), 2) if sd > 0 else None,
        "per_trade_sharpe": round(sr, 3), "skew": round(sk, 2), "kurtosis": round(ku, 2),
        "daily": {"days": len(dv), "sharpe_ann": round(float(dv.mean() / dv.std(ddof=1) * math.sqrt(252)), 2) if dv.std() > 0 else None,
                  "sortino_ann": round(float(dv.mean() / math.sqrt((down ** 2).sum() / len(dv)) * math.sqrt(252)), 2) if len(down) else None,
                  "day_clustered_t": round(float(dv.mean() / dv.std(ddof=1) * math.sqrt(len(dv))), 2) if dv.std() > 0 else None,
                  "lag1_autocorr": round(rho, 3), "effective_days": round(ess, 1)},
        "psr_vs_0": round(psr(sr, n, sk, ku), 3) if not math.isnan(sr) else None,
        "dsr": {"trials_n": trials, "trial_sharpes_used": len(trial_srs), "sr_star_per_trade": round(sr_star, 3),
                "dsr": round(psr(sr, n, sk, ku, sr_star), 3) if not math.isnan(sr) else None},
        "bootstrap": block_bootstrap(dv),
        "drawdown": drawdown(r, t.t_exit.to_numpy()),
        "by_symbol": _group(t, "symbol"),
        "by_session": _group(t, pd.to_datetime(t.t_entry, unit="s", utc=True).dt.hour.map(_session)),
        "by_month_frozen_walk_forward": _group(t, pd.to_datetime(t.t_entry, unit="s", utc=True).dt.strftime("%Y-%m")),
        "by_side": _group(t, t.dir.map({1: "long", -1: "short"})),
        "by_exit": _group(t, "exit_reason"),
    }
    if regimes:
        lab = regime_labels(t)
        out["by_trend_regime"] = _group(lab, "trend_regime")
        out["by_vol_regime"] = _group(lab, "vol_regime")
    m = out["by_month_frozen_walk_forward"]
    out["months_positive"] = f"{sum(v['total_r'] > 0 for v in m.values())}/{len(m)}"
    return out


def pbo_cscv(daily: pd.DataFrame, s: int = 8) -> dict:
    """Probability of Backtest Overfitting (CSCV): split days into s blocks; for every half/half split pick the best
    in-sample Sharpe column and see if it lands below the out-of-sample median (logit <= 0)."""
    x = daily.to_numpy()
    if x.shape[1] < 2 or len(x) < 2 * s:
        return {}
    blocks = np.array_split(np.arange(len(x)), s)

    def sh(a):
        sd = a.std(0, ddof=1)
        return np.where(sd > 0, a.mean(0) / np.where(sd > 0, sd, 1), 0.0)
    logits = []
    for c in itertools.combinations(range(s), s // 2):
        i_is = np.concatenate([blocks[k] for k in c])
        i_os = np.concatenate([blocks[k] for k in range(s) if k not in c])
        best = int(np.argmax(sh(x[i_is])))
        rank = (sh(x[i_os]).argsort().argsort()[best] + 1) / (x.shape[1] + 1)
        logits.append(math.log(rank / (1 - rank)))
    lg = np.array(logits)
    return {"splits": len(lg), "blocks": s, "pbo": round(float((lg <= 0).mean()), 3)}


def holm(p: dict[str, float]) -> dict[str, float]:
    items = sorted(p.items(), key=lambda kv: kv[1])
    out, run = {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (len(items) - i) * v))
        out[k] = round(run, 4)
    return out


def p_two_sided(t: float | None) -> float:
    return 1.0 if t is None else 2 * (1 - _phi(abs(t)))


def to_markdown(name: str, rep: dict) -> str:
    if not rep.get("trades"):
        return f"### {name}\nno trades\n"
    d, b, dd, ds = rep["daily"], rep["bootstrap"], rep["drawdown"], rep["dsr"]
    lines = [f"### {name}",
             f"- trades {rep['trades']}, win {rep['win_rate']:.0%}, **expectancy {rep['expectancy_net_r']:+.3f}R net** "
             f"(before commission+swap {rep['gross_r_before_commission_swap']:+.3f}R; commission {rep['commission_r']:.3f}R; "
             f"swap {rep['swap_r'] if rep['swap_r'] is not None else 'n/a'}R, unknown-swap trades {rep['swap_unknown_trades']}), "
             f"total {rep['total_r']:+.1f}R, PF {rep['profit_factor']}",
             f"- trade t {rep['trade_t']}, day-clustered t {d['day_clustered_t']}, daily Sharpe {d['sharpe_ann']}, Sortino {d['sortino_ann']}, "
             f"lag-1 autocorr {d['lag1_autocorr']}, effective days {d['effective_days']} of {d['days']}",
             f"- PSR(>0) {rep['psr_vs_0']}, **DSR {ds['dsr']}** (SR* {ds['sr_star_per_trade']} per trade from {ds['trials_n']} registered trials)",
             f"- block bootstrap: total R 90% CI {b.get('total_r_ci90')}, P(total<=0) {b.get('p_total_le_0')}, max DD median {b.get('max_dd_r_median')}R / p95 {b.get('max_dd_r_p95')}R",
             f"- drawdown: max {dd.get('max_dd_r')}R, peak->trough {dd.get('max_dd_peak_to_trough_days')} d, recovery {dd.get('max_dd_recovery_days')} d, "
             f"longest under water {dd.get('longest_underwater_days')} d; months positive {rep['months_positive']}"]
    for key in ("by_symbol", "by_session", "by_trend_regime", "by_vol_regime", "by_month_frozen_walk_forward", "by_side", "by_exit"):
        if key in rep:
            lines.append(f"- {key.replace('_', ' ')}: " + "; ".join(f"{k} n{v['n']} {v['avg_r']:+.2f}R ({v['total_r']:+.1f})" for k, v in rep[key].items()))
    return "\n".join(lines) + "\n"


def _selftest() -> None:
    rng = np.random.default_rng(1)
    assert abs(psr(0.0, 100, 0, 3) - 0.5) < 1e-6
    assert psr(0.3, 200, 0, 3) > 0.99 and psr(-0.3, 200, 0, 3) < 0.01
    assert dsr_threshold([0.1, -0.1, 0.05, -0.05], 1000) > dsr_threshold([0.1, -0.1, 0.05, -0.05], 10) > 0
    assert drawdown(np.array([1.0, -2.0, -1.0, 4.0]), np.arange(4) * 86400.0)["max_dd_r"] == 3.0
    assert drawdown(np.array([1.0, -2.0]), np.arange(2) * 86400.0)["max_dd_recovery_days"] == "not recovered"
    noise = pd.DataFrame(rng.normal(0, 1, (400, 10)))
    assert 0.2 < pbo_cscv(noise)["pbo"] < 0.8  # pure noise: PBO near 0.5
    skill = noise.copy(); skill[0] += 0.5
    assert pbo_cscv(skill)["pbo"] < 0.1  # one column truly better: rarely overfit
    assert holm({"a": 0.01, "b": 0.04})["b"] == 0.04 and holm({"a": 0.01, "b": 0.04})["a"] == 0.02
    b = block_bootstrap(rng.normal(0.5, 1, 60))
    assert b["p_total_le_0"] < 0.05
    print("validate selftest ok")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--name", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.csv:
        return _selftest()
    t = pd.read_csv(a.csv)
    rep = report(t, a.start, a.end)
    out = Path(a.csv).with_suffix("")
    Path(f"{out}.validate.json").write_text(json.dumps(rep, indent=1, default=str))
    Path(f"{out}.validate.md").write_text(to_markdown(a.name or out.name, rep))
    print(to_markdown(a.name or out.name, rep))


if __name__ == "__main__":
    main()
