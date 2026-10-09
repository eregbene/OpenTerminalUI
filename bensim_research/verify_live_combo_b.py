"""Replay backend/mt5_strategies/families/donchian_imbalance.py exactly as the live M5 cycle calls it
(completed bars only, 200 M15 / 100 H1 / 100 M5 rows, bid/ask = last M5 close) and compare its triggers
with the research engine's combo B trades (bensim_research.donchian.imb_entry_trades) on the same data.

python -m bensim_research.verify_live_combo_b --start 2026-07-01 --end 2026-10-01 --symbols EURUSD,USDJPY,AUDUSD
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from backend.mt5_strategies.families.donchian_imbalance import evaluate_donchian_imbalance

from . import clean_data as C
from . import data as D
from . import donchian as DC


def _rows(b: D.Bars, a: int, z: int) -> list[dict]:
    return [{"time": datetime.fromtimestamp(int(b.t[k]), timezone.utc).isoformat(), "open": b.o[k], "high": b.h[k], "low": b.l[k], "close": b.c[k]} for k in range(a, z)]


W15, W1 = 200, 100


def replay(sym: str, t0: int, t1: int) -> pd.DataFrame:
    m5 = C.load(sym, "M5")
    m15, h1 = D.resample(m5, "M15"), D.resample(m5, "H1")
    out = []
    for k in np.flatnonzero((m5.t >= t0) & (m5.t < t1)):
        now = int(m5.t[k]) + 300  # cycle runs right after this M5 bar closes
        i15 = int(np.searchsorted(m15.known_at, now, side="right"))  # completed M15 bars
        i1 = int(np.searchsorted(h1.known_at, now, side="right"))
        if i15 < W15 or i1 < W1:
            continue
        px = float(m5.c[k])
        ctx = SimpleNamespace(symbol=sym, broker_symbol=sym, generated_at=datetime.fromtimestamp(now, timezone.utc), regime="neutral",
                              m15_rows=_rows(m15, i15 - W15, i15), h1_rows=_rows(h1, i1 - W1, i1), m5_rows=_rows(m5, k - 99, k + 1),
                              bid=px, ask=px)
        s = evaluate_donchian_imbalance(ctx)
        if s.valid:
            out.append({"symbol": sym, "t_trigger": int(m5.t[k]), "dir": 1 if s.direction == "LONG" else -1, "entry_live": s.proposed_entry,
                        "planned_entry": s.evidence["planned_entry"], "stop": s.stop_loss, "target": s.take_profit, "rr": s.reward_risk})
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-10-01")
    ap.add_argument("--symbols", default="EURUSD,USDJPY,AUDUSD")
    ap.add_argument("--w15", type=int, default=200)
    ap.add_argument("--w1", type=int, default=100)
    a = ap.parse_args()
    global W15, W1
    W15, W1 = a.w15, a.w1
    t0, t1 = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (a.start, a.end))
    for sym in a.symbols.split(","):
        live = replay(sym, t0, t1)
        m15 = D.resample(C.load(sym, "M5"), "M15")
        res = DC.imb_entry_trades(sym, m15, DC.signals(m15), t0, t1)
        matched = 0
        for r in res.itertuples():
            near = live[(live.dir == r.dir) & ((live.t_trigger - r.t_entry).abs() <= 900) & ((live.planned_entry - r.entry).abs() < 1e-9 + abs(r.entry) * 1e-6)] if len(live) else live
            matched += bool(len(near))
        extra = len(live) - matched
        same_stop = np.mean([np.isclose(live.stop[(live.planned_entry - e).abs() < abs(e) * 1e-6], s).any() for e, s in zip(res.entry, res.stop)]) if len(res) and len(live) else float("nan")
        print(f"{sym}: research trades {len(res)} | live-code triggers {len(live)} | research trades matched by a live trigger (+-15 min, same zone) {matched} "
              f"| live triggers without a research trade {extra} | same stop {same_stop:.0%}", flush=True)


if __name__ == "__main__":
    main()
