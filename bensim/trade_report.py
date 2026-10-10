"""Why did trades win or lose? Per-trade context for backtest runs, then win rate / avg R split by each feature.

    python -m bensim.trade_report --runs 64,53,60 [--csv data/bensim_trade_report.csv]

Per trade (all causal, seen at the fill): MFE / MAE in R before the exit, H4 swing + internal trend and D1 trend
vs the trade, entry inside an H4 FVG/OB pointing our way (H4 POI) or the other way (into H4 supply/demand),
high-impact news for the pair within 60 min of the fill / during the trade, fill inside a killzone, hour, weekday.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db, indicators
from .backtest import CURRENCIES, load_m5, resample
from .strategy import trend


def trades(runs: list[int]) -> pd.DataFrame:
    with db.connect() as c:
        rows = c.execute("""select t.run_id, r.start_date, t.symbol, t.dir, extract(epoch from t.t_entry)::bigint, extract(epoch from t.t_exit)::bigint,
                                   t.entry, t.sl, t.tp, t.exit_reason, t.r from backtest_trades t join backtest_runs r on r.id = t.run_id
                            where t.run_id = any(%s) order by t.t_entry""", (runs,)).fetchall()
    return pd.DataFrame(rows, columns=["run", "period", "symbol", "dir", "t_entry", "t_exit", "entry", "sl", "tp", "exit_reason", "r"])


def news(sym: str) -> np.ndarray:
    cur = list(set(CURRENCIES[sym]) | ({"USD"} if sym == "XAUUSD" else set()))
    with db.connect() as c:
        rows = c.execute("select extract(epoch from ts)::bigint from economic_events where impact='high' and currency = any(%s) order by ts", (cur,)).fetchall()
    return np.array([r[0] for r in rows], dtype=np.int64)


def features(t: pd.DataFrame) -> pd.DataFrame:
    out = []
    for sym, g in t.groupby("symbol"):
        t0 = datetime.fromtimestamp(int(g.t_entry.min()) - 400 * 86400, timezone.utc).isoformat()
        m5 = load_m5(sym, t0, datetime.fromtimestamp(int(g.t_exit.max()) + 86400, timezone.utc).isoformat())
        h4, d1 = resample(m5, 14400), resample(m5, 86400)
        ind4 = indicators.compute(h4)
        d1_tr = trend(d1)
        ev = news(sym)
        for row in g.itertuples():
            d, risk = row.dir, abs(row.entry - row.sl)
            a, b = np.searchsorted(m5["t"], row.t_entry), np.searchsorted(m5["t"], row.t_exit)
            hi, lo = m5["high"][a:b + 1].max(), m5["low"][a:b + 1].min()
            k4 = int(np.searchsorted(h4["t"] + 14400, row.t_entry, side="right")) - 1  # last completed H4 bar
            kd = int(np.searchsorted(d1["t"] + 86400, row.t_entry, side="right")) - 1
            obs = ind4["swing"]["obs"] + ind4["internal"]["obs"]
            zone = lambda dd: (indicators._active(ind4["fvgs"], k4, dd, row.entry) or indicators._active(obs, k4, dd, row.entry))  # noqa: E731
            hour = (row.t_entry % 86400) // 3600
            out.append({**row._asdict(),
                        "win": row.r > 0,
                        "mfe_r": round(((hi - row.entry) if d == 1 else (row.entry - lo)) / risk, 2),
                        "mae_r": round(((row.entry - lo) if d == 1 else (hi - row.entry)) / risk, 2),
                        "hours_held": round((row.t_exit - row.t_entry) / 3600, 1),
                        "h4_swing_agrees": bool(ind4["swing"]["trend"][k4] == d),
                        "h4_internal_agrees": bool(ind4["internal"]["trend"][k4] == d),
                        "d1_trend_agrees": bool(d1_tr[kd] == d),
                        "d1_trend_eff": round(float(abs(r20.sum()) / np.abs(r20).sum()), 3) if len(r20 := np.diff(np.log(d1["close"][max(0, kd - 20):kd + 1]))) else 0.0,
                        "h4_poi_with": bool(zone(d)),
                        "h4_zone_against": bool(zone(-d)),
                        "h4_discount": bool((row.entry - (ind4["swing"]["trail_up"][k4] + ind4["swing"]["trail_dn"][k4]) / 2) * d < 0),
                        "news_60m_fill": bool(np.any(np.abs(ev - row.t_entry) <= 3600)),
                        "news_in_trade": bool(np.any((ev >= row.t_entry) & (ev <= row.t_exit))),
                        "fill_in_killzone": any(x <= hour < z for x, z in indicators.KILLZONES),
                        "fill_hour": int(hour), "weekday": datetime.fromtimestamp(row.t_entry, timezone.utc).strftime("%a")})
    return pd.DataFrame(out).sort_values("t_entry")


def split(f: pd.DataFrame, col: str) -> str:
    parts = []
    for v, g in f.groupby(col):
        parts.append(f"{col}={v}: n={len(g)} win={g.win.mean() * 100:.0f}% avgR={g.r.mean():+.2f} totR={g.r.sum():+.1f}")
    return "   |   ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="64,53,60")
    ap.add_argument("--csv", default="data/bensim_trade_report.csv")
    a = ap.parse_args()
    f = features(trades([int(x) for x in a.runs.split(",")]))
    f.to_csv(a.csv, index=False)
    los = f[~f.win]
    print(f"{len(f)} trades, {len(los)} losers. Losers: MFE before stop median {los.mfe_r.median():.2f}R; "
          f"reached +0.5R {(los.mfe_r >= 0.5).mean() * 100:.0f}%, +1R {(los.mfe_r >= 1).mean() * 100:.0f}%, +2R {(los.mfe_r >= 2).mean() * 100:.0f}%; "
          f"median hours to stop {los.hours_held.median():.1f}")
    win = f[f.win]
    print(f"Winners: MAE before target median {win.mae_r.median():.2f}R; went below -0.5R first {(win.mae_r >= 0.5).mean() * 100:.0f}%")
    for col in ("h4_swing_agrees", "h4_internal_agrees", "d1_trend_agrees", "h4_poi_with", "h4_zone_against", "h4_discount",
                "news_60m_fill", "news_in_trade", "fill_in_killzone", "weekday"):
        print(split(f, col))
        for per, g in f.groupby("period"):
            print("    ", per, split(g, col))


if __name__ == "__main__":
    main()
