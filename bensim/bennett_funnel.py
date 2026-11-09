"""Where do Bennett setups die? (forensic phase 2026-11-03; diagnostic only -- no P&L, no parameter change)

    python -m bensim.bennett_funnel          # Aug 3 - Nov 3 2026, bennett_strategy.LIVE, its 5 live pairs + XAUUSD

At every M15 boundary, bennett_strategy.plan() gets exactly the windows the backtester gives it (same code path:
backtest.run_symbol's resample + _win), and a profiler hook records WHICH `return` line of plan() ended the call.
Each line is mapped to the source comment next to it. Output: counts per exit line -> where the funnel narrows.
"""
from __future__ import annotations

import json
import linecache
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import bennett_strategy as bs
from . import registry
from .backtest import WARMUP_SEC, _win, load_m5, resample

START, END = "2026-08-03", "2026-11-03"
SYMS = bs.LIVE_SYMBOLS + ["XAUUSD"]
OUT = Path("reports/experiments/bennett_funnel_2026-11-03")
SRC = bs.__file__


def funnel(sym: str) -> Counter:
    p = bs.LIVE
    hb = p.htf_bars
    warm = max(WARMUP_SEC, 200 * 3600, (hb + 100) * 3600)
    m5 = load_m5(sym, (pd.Timestamp(START, tz="UTC") - pd.Timedelta(seconds=warm)).isoformat(), END)
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    t0 = int(pd.Timestamp(START, tz="UTC").timestamp())
    seen: Counter = Counter()
    last_line = {}

    def prof(frame, event, arg):
        if event == "return" and frame.f_code is bs.plan.__code__:
            last_line["l"] = frame.f_lineno

    for k in np.flatnonzero((m5["t"] >= t0) & ((m5["t"] + 300) % 900 == 0)):  # each completed M15 boundary
        now = int(m5["t"][k]) + 300
        i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
        i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
        last_line.clear()
        sys.setprofile(prof)
        try:
            pl = bs.plan(_win(m15, i15, 300), _win(h1, i1, 200), _win(m5, k + 1, p.m5_bars), sym, p, htf=_win(h1, i1, hb))
        finally:
            sys.setprofile(None)
        seen["TRIGGERED" if pl and pl.get("status") == "TRIGGERED" else f"line {last_line.get('l')}"] += 1
    return seen


def label(key: str) -> str:
    if not key.startswith("line "):
        return key
    n = int(key.split()[1])
    ctx = [linecache.getline(SRC, j).strip() for j in range(max(1, n - 3), n + 1)]
    return f"{key}: " + " | ".join(c for c in ctx if c)[:220]


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(3) as pool:
        res = dict(zip(SYMS, pool.map(funnel, SYMS)))
    tot = sum(res.values(), Counter())
    rows = [{"exit": label(k), "all": v, **{s: res[s].get(k, 0) for s in SYMS}} for k, v in tot.most_common()]
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "funnel.csv", index=False)
    (OUT / "funnel.json").write_text(json.dumps({s: dict(c) for s, c in res.items()}, indent=1))
    registry.append({"kind": "forensic diagnostic (no P&L)", "strategy": "bennett", "period": [START, END], "symbols": SYMS,
                     "code": registry.code_version(), "command": "python -m bensim.bennett_funnel", "outputs": {"dir": str(OUT)}})
    pd.set_option("display.max_colwidth", 140)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
