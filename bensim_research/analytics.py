"""Phases 14-16 helpers: metrics, Monte Carlo, walk-forward selection, parameter stability."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def metrics(t: pd.DataFrame, col: str = "r") -> dict:
    """Trade-level R statistics (+ portfolio % stats when pnl_pct is present)."""
    if t is None or t.empty:
        return {"trades": 0}
    r = t[col].to_numpy(dtype=float)
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = np.maximum.accumulate(np.r_[0, eq])[1:] - eq
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    weeks = max((t.t_entry.max() - t.t_entry.min()) / 604800, 1)
    out = {
        "trades": int(len(r)),
        "win_rate": round(100 * len(wins) / len(r), 1),
        "expectancy_r": round(float(r.mean()), 4),
        "median_r": round(float(np.median(r)), 3),
        "net_r": round(float(r.sum()), 2),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        "t_stat": round(float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))), 2) if len(r) > 2 and r.std() > 0 else None,
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else None,
        "max_dd_r": round(float(dd.max()), 2),
        "max_consec_losses": int(streak),
        "trades_per_week": round(len(r) / weeks, 3),
        "trades_per_month": round(len(r) / weeks * 4.345, 2),
    }
    if "mfe_r" in t:
        out["avg_mfe_r"] = round(float(t.mfe_r.mean()), 3)
        out["avg_mae_r"] = round(float(t.mae_r.mean()), 3)
    if "hold_bars" in t:
        out["avg_hold_bars"] = round(float(t.hold_bars.mean()), 1)
    if "r_gross" in t:
        out["expectancy_gross_r"] = round(float(t.r_gross.mean()), 4)
        out["avg_cost_r"] = round(float(t.cost_r.mean()), 4)
    if "pnl_pct" in t:
        out.update(equity_stats(t))
    return out


def daily_returns(t: pd.DataFrame) -> pd.Series:
    day = pd.to_datetime(t.t_exit, unit="s").dt.normalize()
    s = t.groupby(day).pnl_pct.sum()
    idx = pd.bdate_range(s.index.min(), s.index.max())
    return s.reindex(idx, fill_value=0.0)


def equity_stats(t: pd.DataFrame) -> dict:
    """Portfolio % stats on realised daily P&L (additive % of starting equity, no compounding)."""
    d = daily_returns(t)
    eq = d.cumsum()
    dd = eq.cummax().clip(lower=0) - eq
    years = max(len(d) / 252, 1 / 252)
    ann = eq.iloc[-1] / years
    sd = d.std(ddof=1)
    downside = d[d < 0].std(ddof=1) if (d < 0).sum() > 1 else np.nan
    mdd = float(max(dd.max(), 0))
    ex = pd.to_datetime(t.t_exit, unit="s")
    week = t.groupby(ex.dt.to_period("W")).pnl_pct.sum()
    month = t.groupby(ex.dt.to_period("M")).pnl_pct.sum()
    # average drawdown = mean depth over days spent below a prior peak
    under = dd[dd > 0]
    return {
        "net_pct": round(float(eq.iloc[-1]), 2),
        "annual_pct": round(float(ann), 2),
        "max_dd_pct": round(mdd, 2),
        "avg_dd_pct": round(float(under.mean()), 2) if len(under) else 0.0,
        "sharpe": round(float(d.mean() / sd * math.sqrt(252)), 2) if sd > 0 else None,
        "sortino": round(float(d.mean() / downside * math.sqrt(252)), 2) if downside and downside > 0 else None,
        "calmar": round(float(ann / mdd), 2) if mdd > 0 else None,
        "recovery_factor": round(float(eq.iloc[-1] / mdd), 2) if mdd > 0 else None,
        "worst_day_pct": round(float(d.min()), 2),
        "worst_week_pct": round(float(week.min()), 2),
        "worst_month_pct": round(float(month.min()), 2),
    }


def by(t: pd.DataFrame, col: str, min_n: int = 1) -> dict:
    if t.empty:
        return {}
    out = {}
    for k, g in t.groupby(col):
        if len(g) >= min_n:
            m = metrics(g)
            out[str(k)] = {x: m.get(x) for x in ("trades", "win_rate", "expectancy_r", "net_r", "profit_factor", "t_stat", "max_dd_r")}
    return out


def periodic(t: pd.DataFrame, freq: str) -> dict:
    if t.empty:
        return {}
    ex = pd.to_datetime(t.t_exit, unit="s").dt.to_period(freq)
    col = "pnl_pct" if "pnl_pct" in t else "r"
    g = t.groupby(ex)[col].agg(["sum", "count"])
    return {str(k): {"sum": round(float(v["sum"]), 2), "trades": int(v["count"])} for k, v in g.iterrows()}


def monte_carlo(r: np.ndarray, risk_pct: float = 0.5, runs: int = 10000, seed: int = 7, slip_r: float = 0.05) -> dict:
    """Shuffle trade order (+ uniform extra slippage up to slip_r per trade, + 10% random outcome
    flips of winners to -1R as an execution-failure stress). Drawdowns in % at fixed risk."""
    if len(r) < 5:
        return {}
    rng = np.random.default_rng(seed)
    out = {}
    for name, stress in (("shuffle", False), ("shuffle+slippage+10%_failed_winners", True)):
        dds, streaks, finals = [], [], []
        for _ in range(runs):
            x = rng.permutation(r)
            if stress:
                x = x - rng.uniform(0, slip_r, len(x))
                flip = (x > 0) & (rng.random(len(x)) < 0.10)
                x[flip] = -1.0
            eq = np.cumsum(x) * risk_pct
            dds.append(float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()))
            losses = (x <= 0).astype(int)
            best = cur = 0
            for v in losses:
                cur = cur + 1 if v else 0
                best = max(best, cur)
            streaks.append(best)
            finals.append(eq[-1])
        dds, streaks, finals = np.array(dds), np.array(streaks), np.array(finals)
        out[name] = {
            "dd_pct_p50": round(float(np.percentile(dds, 50)), 2),
            "dd_pct_p95": round(float(np.percentile(dds, 95)), 2),
            "dd_pct_p99": round(float(np.percentile(dds, 99)), 2),
            "p_dd_ge_10pct": round(float((dds >= 10).mean()), 4),
            "p_dd_ge_20pct_ruin": round(float((dds >= 20).mean()), 4),
            "losing_streak_p50": int(np.percentile(streaks, 50)),
            "losing_streak_p95": int(np.percentile(streaks, 95)),
            "p_losing_streak_ge_10": round(float((streaks >= 10).mean()), 4),
            "final_pct_p5": round(float(np.percentile(finals, 5)), 2),
            "p_final_negative": round(float((finals < 0).mean()), 4),
        }
    return out


def walk_forward(trade_sets: dict[str, pd.DataFrame], start: int, end: int, train_m: int = 24, test_m: int = 6, min_n: int = 30) -> dict:
    """Rolling selection: in each train window pick the config with the best expectancy (n>=min_n),
    then record that config's trades in the following test window. Reports IS vs OOS honestly."""
    folds = []
    oos = []
    t0 = pd.Timestamp(start, unit="s")
    while True:
        tr_a, tr_b = t0, t0 + pd.DateOffset(months=train_m)
        te_b = tr_b + pd.DateOffset(months=test_m)
        if tr_b.timestamp() >= end:
            break
        best, best_e, best_n = None, -np.inf, 0
        for name, t in trade_sets.items():
            w = t[(t.t_entry >= tr_a.timestamp()) & (t.t_entry < tr_b.timestamp())]
            if len(w) >= min_n and w.r.mean() > best_e:
                best, best_e, best_n = name, float(w.r.mean()), len(w)
        if best is None:
            folds.append({"train": f"{tr_a.date()}..{tr_b.date()}", "selected": None})
        else:
            t = trade_sets[best]
            w = t[(t.t_entry >= tr_b.timestamp()) & (t.t_entry < min(te_b.timestamp(), end))]
            oos.append(w)
            folds.append({
                "train": f"{tr_a.date()}..{tr_b.date()}", "test": f"{tr_b.date()}..{te_b.date()}", "selected": best,
                "is_trades": best_n, "is_expectancy_r": round(best_e, 4),
                "oos_trades": int(len(w)), "oos_expectancy_r": round(float(w.r.mean()), 4) if len(w) else None,
            })
        t0 = t0 + pd.DateOffset(months=test_m)
    all_oos = pd.concat(oos) if oos else pd.DataFrame()
    is_vals = [f["is_expectancy_r"] for f in folds if f.get("selected")]
    return {
        "folds": folds,
        "mean_is_expectancy_r": round(float(np.mean(is_vals)), 4) if is_vals else None,
        "oos": metrics(all_oos) if not all_oos.empty else {"trades": 0},
    }
