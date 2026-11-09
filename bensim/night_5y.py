"""5-year check of idea B (Asian breakout) and B2 (first London hour, stop at the other side) -- user go 2026-11-03.

    python -m bensim.night_5y

DB M5 candles (UTC) -> server time via mt5_time offsets; spread = backtest spread model (Exec defaults) + 0.007%
commission. 6 majors, 2021-11-01..2026-10-26. B2 was chosen on 2026-07-29..11-03, so 2021-11..2026-07 is unseen.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5
from .mt5_time import server_offset_hours
from .night_check import breakout
from .pattern_check import stat

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD"]


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    ex, rows = Exec(), []
    for sym in PAIRS:
        m = load_m5(sym, "2021-11-01", "2026-10-26")
        off = np.array([server_offset_hours(int(t)) for t in m["t"][::288]]).repeat(288)[:len(m["t"])]
        b = {**m, "t": m["t"] + off * 3600, "spread": np.array([_spread(sym, int(t), ex) for t in m["t"]])}
        for name, kw in (("B", {}), ("B2", {"last_hour": 10, "stop_opp": True})):
            ts: list = []
            r = breakout(b, times=ts, **kw)
            rows += [{"idea": name, "sym": sym, "t": t, "r": x} for t, x in zip(ts, r)]
        print(f"  {sym} done", flush=True)
    df = pd.DataFrame(rows)
    df["year"] = pd.to_datetime(df.t, unit="s").dt.year
    df.to_csv("logs/night_5y.csv", index=False)
    seen = df.t >= pd.Timestamp("2026-07-29").timestamp()
    for name, g in df.groupby("idea"):
        print(f"\n{name}: ALL {stat(g.r)}")
        print(f"    unseen (before 2026-07-29) {stat(g.r[~seen[g.index]])}")
        print("    by year: " + "  ".join(f"{y} {x.mean():+.3f}R(n{len(x)})" for y, x in g.groupby('year').r))
        print("    by pair: " + "  ".join(f"{p} {x.sum():+.0f}R" for p, x in g.groupby('sym').r))


if __name__ == "__main__":
    main()
