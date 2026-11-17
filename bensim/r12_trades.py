"""R1/R2 trades (docs/audit/r1_r2_preregistration_2026-11-03.md): fib618 / enigma / imbalance, frozen params,
observed_account costs, live Friday rule per strategy, 2021-10-01 .. 2026-06-30 -> validation and confirmation files.

    python -m bensim.r12_trades
The confirmation file is written but NOT summarised here (it is read once, by r1_news / r2_noise --split confirmation).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import books, registry
from .backtest import exec_profile, run_symbol
from .weekly_check import LIVE as FIB_LIVE

START, SPLIT, END = "2021-10-01", "2024-04-01", "2026-07-01"
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "XAUUSD"]
STRATS = {"fib618": (FIB_LIVE, "imbalance", True), "enigma": (books.ENIGMA, "htf_fvg", False),
          "imbalance": (books.IMB, "imbalance", False)}  # (params, kind, closes Friday 20:00 like live.py)
OUT = Path("reports/experiments/r12_2026-11-03")


def _job(a):
    name, sym = a
    p, kind, fri = STRATS[name]
    ex = exec_profile("observed_account")
    if not fri:
        ex = replace(ex, friday_close_utc=-1)
    t = run_symbol((sym, START, END, p, ex, kind))
    return t.assign(strategy=name) if not t.empty else t


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(3) as pool:
        tr = pd.concat([f for f in pool.map(_job, [(n, s) for n in STRATS for s in SYMS]) if not f.empty], ignore_index=True)
    split = int(pd.Timestamp(SPLIT, tz="UTC").timestamp())
    val, conf = tr[tr.t_entry < split], tr[tr.t_entry >= split]
    val.to_csv(OUT / "trades_validation.csv", index=False)
    conf.to_csv(OUT / "trades_confirmation.csv", index=False)
    registry.append({"kind": "R1/R2 trade generation", "period": [START, END], "split": SPLIT, "symbols": SYMS,
                     "strategies": list(STRATS), "code": registry.code_version(), "data": registry.data_fingerprint(SYMS, START, END),
                     "command": "python -m bensim.r12_trades", "outputs": {"validation": str(OUT / "trades_validation.csv"),
                                                                           "confirmation": str(OUT / "trades_confirmation.csv")},
                     "counts": {"validation": val.groupby("strategy").size().to_dict(), "confirmation": conf.groupby("strategy").size().to_dict()}})
    print("validation trades:", val.groupby("strategy").size().to_dict())
    print("confirmation trades (counts only, not opened):", conf.groupby("strategy").size().to_dict())


if __name__ == "__main__":
    main()
