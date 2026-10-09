"""Run every Bensim experiment and write data/research/bensim_report.json (+ trade journals).

python -m bensim_research.run [--stack H|M] [--workers 3]

Decisions (config choice) only ever look at DEV (< 2024-01-01); HOLDOUT (2024-01-01 .. 2026-08-14)
is scored once for the pre-declared candidates and never used to pick anything.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from . import analytics as A
from . import backtest as B
from . import data as D
from . import strategy as S

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research"
ART = OUT / "artifacts" / "bensim"
HOLDOUT = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())

FULL = ["htf_ok", "ctx_ok", "struct_ok", "sweep", "disp_ok", "loc_ok", "wy_ok"]
CORE = ["htf_ok", "struct_ok", "sweep"]
LADDER = [("L1 FVG", []), ("L2 +HTF", ["htf_ok"]), ("L3 +structure", ["htf_ok", "struct_ok"]),
          ("L4 +sweep", ["htf_ok", "struct_ok", "sweep"]), ("L5 +displacement", ["htf_ok", "struct_ok", "sweep", "disp_ok"]),
          ("L6 +location(SD/HTF-FVG/PD)", ["htf_ok", "struct_ok", "sweep", "disp_ok", "loc_ok"]),
          ("L7 +Wyckoff/ctx (FULL)", FULL), ("L8 +session", FULL + ["sess_ok"])]
SINGLE = [("T1 FVG alone", []), ("T2 FVG+HTF", ["htf_ok"]), ("T3 FVG+structure", ["struct_ok"]),
          ("T4 FVG+supply/demand", ["sd"]), ("T5 FVG+liquidity sweep", ["sweep"]), ("T6 FVG+Wyckoff", ["wy_aligned"]),
          ("T6b FVG+displacement", ["disp_ok"]), ("T6c FVG+location", ["loc_ok"]), ("T6d FVG+London/NY", ["sess_ok"])]

cA2 = B.Cfg(entry="A", stop="C", tp="R", rr=2.0)
cB2 = B.Cfg(entry="B", stop="C", tp="R", rr=2.0)
cC2 = B.Cfg(entry="C", stop="CONF", tp="R", rr=2.0)
cV1 = B.Cfg(entry="C", stop="CONF", tp="LIQ", min_rr=1.5)  # Bensim Baseline v1


def experiments() -> list[tuple[str, list, dict, B.Cfg]]:
    """(name, required boolean columns, extra filters, cfg)."""
    E: list = []
    for n, f in SINGLE:
        E.append((f"single|{n}|A-2R", f, {}, cA2))
    for n, f in LADDER:
        E.append((f"ladder|{n}|A-2R", f, {}, cA2))
        E.append((f"ladder|{n}|C-2R", f, {}, cC2))
    for col in FULL:
        rest = [x for x in FULL if x != col]
        E.append((f"dropone|FULL-{col}|A-2R", rest, {}, cA2))
        E.append((f"dropone|FULL-{col}|C-2R", rest, {}, cC2))
    for fs_name, fs in (("CORE", CORE), ("FULL", FULL)):
        E.append((f"entry|{fs_name}|A first-touch", fs, {}, cA2))
        E.append((f"entry|{fs_name}|B 50%-CE", fs, {}, cB2))
        E.append((f"entry|{fs_name}|C confirmed", fs, {}, cC2))
        E.append((f"entry|{fs_name}|C confirmed (no LTF sweep)", fs, {}, B.Cfg(entry="C", stop="CONF", tp="R", rr=2.0, confirm_sweep=False)))
    for s in ("A", "B", "C", "D"):
        E.append((f"stop|CORE|A-entry stop {s}", CORE, {}, B.Cfg(entry="A", stop=s, tp="R", rr=2.0)))
    for s in ("CONF", "A", "C"):
        E.append((f"stop|CORE|C-entry stop {s}", CORE, {}, B.Cfg(entry="C", stop=s, tp="R", rr=2.0)))
    for ent, base in (("C", cC2), ("A", cA2)):
        for rr in (1.5, 2.0, 2.5, 3.0):
            E.append((f"tp|CORE|{ent} fixed {rr}R", CORE, {}, B.variant(base, tp="R", rr=rr)))
        for mr in (1.5, 2.0, 2.5, 3.0):
            E.append((f"tp|CORE|{ent} liquidity minRR {mr}", CORE, {}, B.variant(base, tp="LIQ", min_rr=mr)))
        E.append((f"tp|CORE|{ent} partial+runner", CORE, {}, B.variant(base, tp="PARTIAL")))
        E.append((f"tp|CORE|{ent} adaptive", CORE, {}, B.variant(base, tp="ADAPT", min_rr=1.5)))
        E.append((f"mgmt|CORE|{ent} 3R no-BE", CORE, {}, B.variant(base, tp="R", rr=3.0)))
        E.append((f"mgmt|CORE|{ent} 3R BE@1R", CORE, {}, B.variant(base, tp="R", rr=3.0, be=True)))
        E.append((f"mgmt|CORE|{ent} 3R ATR-trail", CORE, {}, B.variant(base, tp="R", rr=3.0, trail="atr")))
        E.append((f"mgmt|CORE|{ent} 3R structure-trail", CORE, {}, B.variant(base, tp="R", rr=3.0, trail="struct")))
    for sc in ("optimistic", "base", "conservative"):
        E.append((f"cost|FULL|v1 {sc}", FULL, {}, B.variant(cV1, scenario=sc)))
        E.append((f"cost|CORE|C-2R {sc}", CORE, {}, B.variant(cC2, scenario=sc)))
        E.append((f"cost|T1|A-2R {sc}", [], {}, B.variant(cA2, scenario=sc)))
    for m in ("CONTINUATION", "REVERSAL"):
        E.append((f"mode|CORE|{m} C-2R", CORE, {"mode": m}, cC2))
        E.append((f"mode|CORE|{m} A-2R", CORE, {"mode": m}, cA2))
    for v in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8):
        E.append((f"stability|fvg_size>={v}|CORE C-2R", CORE, {"size_min": v}, cC2))
        E.append((f"stability|fvg_size>={v}|T1 A-2R", [], {"size_min": v}, cA2))
    for sc in range(5, 13):
        E.append((f"stability|score>={sc}|A-2R", [], {"score_min": sc}, cA2))
        E.append((f"stability|score>={sc}|C-2R", [], {"score_min": sc}, cC2))
    E.append(("baseline|Bensim Baseline v1|C-CONF-LIQ1.5", FULL, {}, cV1))
    E.append(("baseline|Bensim v1 CORE|C-CONF-LIQ1.5", CORE, {}, cV1))
    return E


def select(c: pd.DataFrame, cols: list, extra: dict) -> pd.DataFrame:
    m = np.ones(len(c), dtype=bool)
    for col in cols:
        m &= c[col].to_numpy(dtype=bool)
    if "mode" in extra:
        m &= (c["mode"] == extra["mode"]).to_numpy()
    if "size_min" in extra:
        m &= (c.size_atr >= extra["size_min"]).to_numpy()
    if "score_min" in extra:
        m &= (c.score >= extra["score_min"]).to_numpy()
    return c[m]


def _low_priority() -> None:
    if os.name == "nt":  # live DEMO always beats research CPU
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x40)  # IDLE
    else:
        os.nice(19)


def run_symbol(args: tuple) -> dict:
    symbol, stack_name, fvg_db = args
    t0 = time.time()
    stack = S.STACKS[stack_name]
    ctx = S.build(symbol, stack, with_fvg_db=fvg_db)
    out: dict = {"symbol": symbol, "trades": {}, "rejects": {}, "cands": len(ctx.cands), "fvg_db": {}}
    exps = experiments() if stack_name == "H" else [e for e in experiments() if e[0].split("|")[0] in ("single", "ladder", "entry", "baseline", "cost")]
    for name, cols, extra, cfg in exps:
        tr, rej = B.simulate(ctx, select(ctx.cands, cols, extra), cfg)
        out["trades"][name] = tr
        out["rejects"][name] = rej
    if stack_name == "H":  # parameter stability for the swing definition (needs a rebuild)
        for k in (3, 4):
            c2 = S.build(symbol, stack, k_setup=k)
            for n, cols, cfg in (("CORE C-2R", CORE, cC2), ("T1 A-2R", [], cA2), ("FULL v1", FULL, cV1)):
                tr, _ = B.simulate(c2, select(c2.cands, cols, {}), cfg)
                out["trades"][f"stability|swing_k={k}|{n}"] = tr
        for n, cols, cfg in (("CORE C-2R", CORE, cC2), ("T1 A-2R", [], cA2), ("FULL v1", FULL, cV1)):
            out["trades"][f"stability|swing_k=2|{n}"] = out["trades"][{"CORE C-2R": "entry|CORE|C confirmed", "T1 A-2R": "single|T1 FVG alone|A-2R", "FULL v1": "baseline|Bensim Baseline v1|C-CONF-LIQ1.5"}[n]]
    out["fvg_db"] = ctx.fvg_db
    out["base_start"], out["base_end"] = int(ctx.base.t[0]), int(ctx.base.t[-1])
    out["seconds"] = round(time.time() - t0, 1)
    print(f"{symbol} stack {stack_name}: {len(ctx.cands)} candidates, {out['seconds']}s", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="H")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--symbols", default=",".join(D.SYMBOLS))
    args = ap.parse_args()
    _low_priority()
    for s in args.symbols.split(","):  # populate the cache serially (one DB reader)
        D.load(s, S.STACKS[args.stack].base)
    dest = ART / args.stack
    dest.mkdir(parents=True, exist_ok=True)
    jobs = [(s, args.stack, args.stack == "H") for s in args.symbols.split(",")]
    frames, rejects, meta, fvg = [], {}, {}, []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_low_priority) as ex:
        for res in ex.map(run_symbol, jobs):
            sym = res["symbol"]
            meta[sym] = {"candidates": res["cands"], "start": res["base_start"], "end": res["base_end"], "seconds": res["seconds"]}
            rejects[sym] = res["rejects"]
            for name, tr in res["trades"].items():
                if not tr.empty:
                    frames.append(tr.assign(exp=name))
            for tf, f in res["fvg_db"].items():
                fvg.append(f.drop(columns=["j"]))
    pd.concat(frames, ignore_index=True).to_csv(dest / "trades.csv.gz", index=False)
    if fvg:
        pd.concat(fvg, ignore_index=True).to_csv(dest / "fvg_research.csv.gz", index=False)
    (dest / "meta.json").write_text(json.dumps({"symbols": meta, "rejects": rejects,
                                                "experiments": {n: {"filters": c, "extra": e, "cfg": asdict(g)} for n, c, e, g in experiments()},
                                                "stack": asdict(S.STACKS[args.stack])}, indent=1, default=str))
    print("saved", dest)


if __name__ == "__main__":
    main()
