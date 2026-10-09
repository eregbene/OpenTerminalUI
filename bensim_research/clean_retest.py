"""Re-test of the imbalance-video strategy + Donchian on CLEAN data (ForexSB/Dukascopy M5, 2024-02 -> today).

Same rules as before (imb_wf.pool / donchian.signals); only the data changes. Costs reported two ways:
  net       = repo broker cost model (spread+commission+slippage, backtest._costs "base")
  net_dk    = Dukascopy's own spread on the entry bar only (lower bound: no commission, no slippage)

python -m bensim_research.clean_retest
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import clean_data as C
from . import data as D
from . import donchian as DC
from . import imb_wf as W
from .run import OUT, _low_priority

START = int(pd.Timestamp("2024-02-12", tz="UTC").timestamp())  # one week after the M5 file starts (zone warm-up)


def one(sym: str) -> pd.DataFrame:
    base = C.load(sym, "M5")
    p = W.pool(sym, base)
    p = p[(p.tmode == "near") & (p.t_entry >= START)].copy()
    sp = C.load_spread(sym, "M5")
    k = np.clip(np.searchsorted(base.t, p.t_entry.to_numpy(), side="right") - 1, 0, len(base) - 1)
    risk = (p.entry - p.stop).abs().to_numpy()
    p["cost_dk"] = sp[k] / risk
    p["r_dk"] = p.r_gross - p.cost_dk
    sig = DC.signals(D.resample(base, "M15"))[["t_signal", "dir"]].sort_values("t_signal")
    ts, ds = sig.t_signal.to_numpy(), sig.dir.to_numpy()
    j = np.searchsorted(ts, p.t_entry.to_numpy(), side="right") - 1
    jj = np.maximum(j, 0)
    p["donchian_agrees"] = (j >= 0) & (p.t_entry.to_numpy() - ts[jj] <= DC.FILTER_WINDOW) & (ds[jj] == p.dir.to_numpy())
    p["data_hole"] = D.overlaps_hole(D.holes(base), p.t_entry.to_numpy(), p.t_exit.to_numpy())
    # Donchian alone on the same clean data
    m15 = D.resample(base, "M15")
    dsig = DC.signals(m15)
    dt = DC.donchian_trades(sym, m15, dsig, START, int(base.t[-1]))
    if not dt.empty:
        k2 = np.clip(np.searchsorted(base.t, dt.t_entry.to_numpy(), side="right") - 1, 0, len(base) - 1)
        dt["r_dk"] = dt.r_gross - sp[k2] / (dt.entry - dt.stop).abs()
        dt["tf"] = "M15"
        dt["data_hole"] = D.overlaps_hole(D.holes(base), dt.t_entry.to_numpy(), dt.t_exit.to_numpy())
    print(sym, len(p), "imbalance trades,", len(dt), "donchian trades", flush=True)
    return pd.concat([p.assign(variant="imbalance"), dt.assign(variant="donchian alone")], ignore_index=True)


def summ(g: pd.DataFrame) -> dict:
    if len(g) < 3:
        return {"trades": len(g)}
    out = {"trades": len(g), "win%": round(100 * float((g.exit_reason == "target").mean()), 1), "gross": round(float(g.r_gross.mean()), 3)}
    for col, name in (("r", "net"), ("r_dk", "net_dk")):
        r = g[col].to_numpy()
        out[name] = round(float(r.mean()), 3)
        out[f"t_{name}"] = round(float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), 2)
    out["total_net"] = round(float(g.r.sum()), 1)
    return out


def main() -> None:
    import sys
    if "--from-csv" in sys.argv:  # re-score saved trades (e.g. after a cost-model fix) without re-running
        t = pd.read_csv(W.DEST / "clean_retest_trades.csv.gz")
        gold = t.symbol == "XAUUSD"
        t.loc[gold & (t.variant == "imbalance"), "cost_dk"] /= 10  # 2026-10-09 fix: gold spread points are 0.001, not 0.01
        t["r_dk"] = np.where(gold, t.r_gross - t.cost_dk.fillna((t.r_gross - t.r_dk) / 10), t.r_dk)
        return report(t)
    _low_priority()
    with ProcessPoolExecutor(3, initializer=_low_priority) as ex:
        t = pd.concat(ex.map(one, D.SYMBOLS), ignore_index=True)
    t = t[~t.data_hole.astype(bool)]
    t.to_csv(W.DEST / "clean_retest_trades.csv.gz", index=False)
    report(t)


def report(t: pd.DataFrame) -> None:
    t["year"] = pd.to_datetime(t.t_entry, unit="s").dt.year
    hour = (t.t_entry % 86400) // 3600
    imb = t.variant == "imbalance"
    core = imb & (t.tf != "M30") & (t.minmult_val > 8) & (t.symbol != "GBPUSD")
    sets = {
        "imbalance, all zones (video rules), M30/H1/H4": imb,
        "imbalance H1/H4 only": imb & t.tf.isin(["H1", "H4"]),
        "CORE (no M30, stop > 8 spreads, no GBPUSD)": core,
        "CORE + no entry 12-20 UTC": core & ~hour.between(12, 19),
        "CORE + no 12-20 UTC + skip HTF-aligned": core & ~hour.between(12, 19) & (t.htf == 0),
        "imbalance H1/H4 + donchian direction (combo A)": imb & t.tf.isin(["H1", "H4"]) & t.donchian_agrees.astype(bool),
        "CORE + donchian direction": core & t.donchian_agrees.astype(bool),
        "donchian alone (M15)": t.variant == "donchian alone",
    }
    rep = {}
    for name, m in sets.items():
        g = t[m]
        rep[name] = {"all": summ(g), **{str(y): summ(x) for y, x in g.groupby("year")},
                     "by_tf": {k: summ(x) for k, x in g.groupby("tf")}}
    (OUT / "clean_retest_report.json").write_text(json.dumps(rep, indent=1))
    for name, v in rep.items():
        a = v["all"]
        yrs = " | ".join(f"{y}: {v[y].get('net', '-'):+} (n={v[y]['trades']})" for y in ("2024", "2025", "2026") if y in v and "net" in v[y])
        print(f"\n{name}\n   all: n={a['trades']} win {a.get('win%')}% gross {a.get('gross')} net {a.get('net')} (t {a.get('t_net')}) "
              f"net_dk {a.get('net_dk')} (t {a.get('t_net_dk')}) total {a.get('total_net')}R\n   {yrs}")


if __name__ == "__main__":
    main()
