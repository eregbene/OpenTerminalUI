"""Phase 16: turn saved trades into data/research/bensim_report_<stack>.json + docs/bensim-backtest-report.md.

python -m bensim_research.report [--stack H]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import analytics as A
from . import backtest as B
from . import data as D
from .run import ART, HOLDOUT, OUT

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Pre-declared selection pool (fixed before looking at any result): the config "picked" on DEV
# and the walk-forward choose only from these.
POOL = [
    "entry|CORE|A first-touch", "entry|CORE|B 50%-CE", "entry|CORE|C confirmed", "entry|CORE|C confirmed (no LTF sweep)",
    "entry|FULL|A first-touch", "entry|FULL|B 50%-CE", "entry|FULL|C confirmed", "entry|FULL|C confirmed (no LTF sweep)",
    "tp|CORE|C fixed 1.5R", "tp|CORE|C fixed 2.5R", "tp|CORE|C fixed 3.0R", "tp|CORE|A fixed 1.5R",
    "stop|CORE|C-entry stop A", "stop|CORE|A-entry stop B", "mode|CORE|CONTINUATION C-2R", "mode|CORE|CONTINUATION A-2R",
    "ladder|L5 +displacement|C-2R", "ladder|L6 +location(SD/HTF-FVG/PD)|C-2R", "ladder|L2 +HTF|C-2R", "ladder|L3 +structure|C-2R",
    "baseline|Bensim Baseline v1|C-CONF-LIQ1.5", "baseline|Bensim v1 CORE|C-CONF-LIQ1.5",
]
KEY = ("trades", "win_rate", "expectancy_r", "expectancy_gross_r", "avg_cost_r", "profit_factor", "t_stat", "net_r", "max_dd_r", "trades_per_week")


def compact(m: dict) -> dict:
    return {k: m.get(k) for k in KEY}


def split(t: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"dev": t[t.t_entry < HOLDOUT], "holdout": t[t.t_entry >= HOLDOUT], "all": t}


def table(trades: pd.DataFrame, prefix: str) -> dict:
    out = {}
    for name, g in trades[trades.exp.str.startswith(prefix)].groupby("exp", sort=False):
        out[name.split("|", 1)[1]] = {k: compact(A.metrics(v)) for k, v in split(g).items()}
    return out


def svg_curve(series: dict[str, pd.Series], title: str, path: Path, ylabel: str) -> None:
    """Tiny dependency-free SVG line chart (matplotlib is not installed here)."""
    W, H, P = 900, 320, 50
    allv = pd.concat(series.values())
    lo, hi = float(min(allv.min(), 0)), float(max(allv.max(), 0))
    hi = hi if hi > lo else lo + 1
    t0 = min(s.index.min() for s in series.values()).value
    t1 = max(s.index.max() for s in series.values()).value
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea"]

    def xy(ts, v):
        x = P + (ts.value - t0) / max(t1 - t0, 1) * (W - 2 * P)
        y = H - P - (v - lo) / (hi - lo) * (H - 2 * P)
        return f"{x:.1f},{y:.1f}"

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
             f'<rect width="{W}" height="{H}" fill="white"/>', f'<text x="{P}" y="20" font-size="14">{title}</text>']
    y0 = H - P - (0 - lo) / (hi - lo) * (H - 2 * P)
    parts.append(f'<line x1="{P}" x2="{W - P}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="#999" stroke-dasharray="4"/>')
    parts.append(f'<text x="5" y="{P}">{hi:.1f}</text><text x="5" y="{H - P}">{lo:.1f}</text><text x="5" y="{H / 2}">{ylabel}</text>')
    for i, (name, s) in enumerate(series.items()):
        s = s.iloc[:: max(1, len(s) // 600)]
        pts = " ".join(xy(ts, v) for ts, v in s.items())
        parts.append(f'<polyline fill="none" stroke="{colors[i % 4]}" stroke-width="1.5" points="{pts}"/>')
        parts.append(f'<text x="{W - P - 260}" y="{40 + 16 * i}" fill="{colors[i % 4]}">{name}</text>')
    parts.append(f'<text x="{P}" y="{H - 15}">{pd.Timestamp(t0).date()}</text><text x="{W - P - 70}" y="{H - 15}">{pd.Timestamp(t1).date()}</text></svg>')
    path.write_text("\n".join(parts))


def news_variants(t: pd.DataFrame) -> dict:
    if t.empty:
        return {}
    win, nfp = D.us_data_window(t.t_entry.to_numpy())
    near = win | nfp & ((t.t_entry.to_numpy() % 86400) // 3600 >= 11) & ((t.t_entry.to_numpy() % 86400) // 3600 <= 15)
    a = t
    b = t[~near & ~t.news_disp.to_numpy(dtype=bool)]
    c = t[~near | t.news_disp.to_numpy(dtype=bool)]
    return {"A no filter": compact(A.metrics(a)), "B avoid 08:30 ET window/NFP + news-displacement setups": compact(A.metrics(b)),
            "C avoid window but allow news-displacement setups": compact(A.metrics(c))}


def fvg_research(f: pd.DataFrame) -> dict:
    if f.empty:
        return {}
    out: dict = {}
    f = f.assign(touched=f.touch.notna(), ce_hit=f.ce.notna(), filled=f.full.notna(), inval=f.invalid.notna(),
                 size_bucket=pd.cut(f.size_atr, [0, 0.1, 0.25, 0.5, 1, 100], labels=["<0.1", "0.1-0.25", "0.25-0.5", "0.5-1", ">1"]))

    def rates(g: pd.DataFrame) -> dict:
        t = g[g.touched]
        return {"n": int(len(g)), "p_never_return": round(1 - g.touched.mean(), 3), "p_touch": round(g.touched.mean(), 3),
                "p_50pct": round(g.ce_hit.mean(), 3), "p_full_fill": round(g.filled.mean(), 3), "p_invalidated": round(g.inval.mean(), 3),
                "p_respected_given_touch": round(t.respected.mean(), 3) if len(t) else None,
                "median_bars_to_touch": float(t.touch.median()) if len(t) else None,
                "median_mfe_atr_after_touch": round(float(t.mfe_atr.median()), 3) if len(t) else None,
                "median_mae_atr_after_touch": round(float(t.mae_atr.median()), 3) if len(t) else None}

    out["by_tf"] = {tf: rates(g) for tf, g in f.groupby("tf")}
    for col in ("symbol", "session", "regime", "phase", "htf_aligned", "tf_trend_aligned", "size_bucket", "dir", "year"):
        out[f"by_tf_{col}"] = {tf: {str(k): rates(gg) for k, gg in g.groupby(col, observed=True) if len(gg) >= 50} for tf, g in f.groupby("tf")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="H")
    args = ap.parse_args()
    src = ART / args.stack
    trades = pd.read_csv(src / "trades.csv.gz")
    meta = json.loads((src / "meta.json").read_text())
    rep: dict = {"stack": meta["stack"], "symbols": meta["symbols"], "holdout_start": "2024-01-01"}
    for grp in ("single", "ladder", "dropone", "entry", "stop", "tp", "mgmt", "cost", "mode", "stability", "baseline"):
        rep[grp] = table(trades, grp + "|")

    sets = {n: trades[trades.exp == n] for n in POOL if (trades.exp == n).any()}
    dev_scores = {n: A.metrics(t[t.t_entry < HOLDOUT]) for n, t in sets.items()}
    eligible = {n: m for n, m in dev_scores.items() if m.get("trades", 0) >= 100}
    pick = max(eligible, key=lambda n: eligible[n]["expectancy_r"]) if eligible else None
    rep["selection"] = {"pool": POOL, "rule": "highest DEV expectancy with >=100 DEV trades", "picked": pick,
                        "dev": compact(dev_scores[pick]) if pick else None,
                        "holdout": compact(A.metrics(sets[pick][sets[pick].t_entry >= HOLDOUT])) if pick else None}
    lo, hi = int(trades.t_entry.min()), int(trades.t_entry.max())
    rep["walk_forward"] = A.walk_forward(sets, lo, hi)

    focus = [x for x in dict.fromkeys([pick, "baseline|Bensim Baseline v1|C-CONF-LIQ1.5", "entry|CORE|C confirmed", "single|T1 FVG alone|A-2R"]) if x]
    rep["focus"] = {}
    curves, dds = {}, {}
    for name in focus:
        t = trades[trades.exp == name]
        if t.empty:
            continue
        f: dict = {"by_symbol": {k: {p: compact(A.metrics(v)) for p, v in split(g).items()} for k, g in t.groupby("symbol")}}
        for col in ("session", "regime", "phase", "mode", "dir", "vol", "exit_reason"):
            f[f"by_{col}"] = A.by(t, col)
        f["by_year"] = A.by(t.assign(year=pd.to_datetime(t.t_entry, unit="s").dt.year), "year")
        f["news"] = news_variants(t)
        f["trades_per_week_by_symbol"] = {k: round(len(g) / ((hi - lo) / 604800), 3) for k, g in t.groupby("symbol")}
        for label, rk in (("fixed_0.5pct", B.Risk()), ("adaptive", B.Risk(adaptive=True))):
            p = B.portfolio(t, rk)
            if p.empty:
                continue
            f[f"portfolio_{label}"] = {"all": A.metrics(p), "holdout": A.metrics(p[p.t_entry >= HOLDOUT]),
                                       "states": p.state.value_counts().to_dict(), "skipped_by_risk_rules": int(len(t) - len(p))}
            if label == "fixed_0.5pct":
                f["monthly_pct"] = A.periodic(p, "M")
                f["yearly_pct"] = A.periodic(p, "Y")
                d = A.daily_returns(p)
                curves[name.split("|")[1]] = d.cumsum()
                eq = d.cumsum()
                dds[name.split("|")[1]] = -(eq.cummax().clip(lower=0) - eq)
                f["monte_carlo"] = A.monte_carlo(p.r.to_numpy())
        rejects: dict = {}
        for sym, rj in meta["rejects"].items():
            for why, n in rj.get(name, {}).items():
                rejects[why] = rejects.get(why, 0) + n
        f["no_trade_reasons"] = dict(sorted(rejects.items(), key=lambda kv: -kv[1]))
        rep["focus"][name] = f
        t.to_csv(src / f"journal_{name.split('|')[1].replace(' ', '_').replace('/', '-')}.csv", index=False)
    if curves:
        svg_curve(curves, f"Bensim stack {args.stack}: cumulative % (0.5% risk, portfolio rules)", DOCS / f"bensim_equity_{args.stack}.svg", "% equity")
        svg_curve(dds, "Drawdown (%)", DOCS / f"bensim_drawdown_{args.stack}.svg", "% DD")
    fvg_path = src / "fvg_research.csv.gz"
    if fvg_path.exists():
        rep["fvg_research"] = fvg_research(pd.read_csv(fvg_path))
    (OUT / f"bensim_report_{args.stack}.json").write_text(json.dumps(rep, indent=1, default=str))
    print("wrote", OUT / f"bensim_report_{args.stack}.json")


if __name__ == "__main__":
    main()
