"""Audit P2 v2 (docs/audit/p2_protocol_v2_2026-11-03.md): each strategy's own live Friday rule, built from the P2 v1
trade files (Friday-close and weekends-held runs, otherwise identical) + portfolio correlation / exposure / one-per-pair.

    python -m bensim.audit_p2_v2            # needs reports/experiments/p2_2026-11-03/trades_*.csv from bensim.audit_p2
    python -m bensim.audit_p2_v2 --selftest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import registry, validate
from .backtest import CURRENCIES

SRC = Path("reports/experiments/p2_2026-11-03")
OUT = Path("reports/experiments/p2v2_2026-11-03")
START, END = "2026-08-03", "2026-11-03"
STRATS = ["fib618", "imbalance", "enigma", "h1_fvg", "bennett", "pullback", "orderflow", "gf"]
WEEKEND_HOLD = {"imbalance", "enigma"}  # books.py: no Friday close live; everything else -> live.py Friday 20:00 UTC
LIVE_SET = ["fib618", "bennett", "imbalance", "enigma"]  # books.py tie order for the one-per-pair rule
# longer-run evidence already on file (memory / logs), used only for the "contradicts" check of the recommendation
LONG_RUN = {"fib618": "negative (25 pairs x 14y -0.048R; 0/15 years)", "imbalance": "flat (5y +0.048R, t 0.96)",
            "enigma": "flat (5y ~ break-even)", "bennett": "negative (5y setup DB after costs)",
            "pullback": "negative (earlier 3m 955 tr -0.17R t -3.8)", "orderflow": "negative/flat (earlier 3m variants 0 to -0.22R)",
            "h1_fvg": "none on file", "gf": "none on file"}


def one_per_pair(t: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """Live books rule: a symbol already held by any trade in the set blocks new trades on it until that trade exits."""
    rank = {s: i for i, s in enumerate(order)}
    t = t.assign(_rk=t.strategy.map(rank)).sort_values(["t_entry", "_rk"])
    busy, keep = {}, []
    for i, x in t.iterrows():
        if busy.get(x.symbol, -1) >= x.t_entry:  # still held (exit at or after this entry)
            continue
        keep.append(i)
        busy[x.symbol] = x.t_exit
    return t.loc[keep].drop(columns="_rk")


def exposure(t: pd.DataFrame) -> dict:
    """Max concurrent trades, max open risk in R (1R each), max |net| exposure per currency in R units."""
    ev = []
    for x in t.itertuples():
        base, quote = CURRENCIES.get(x.symbol, (x.symbol[:3], x.symbol[3:]))
        ev.append((x.t_entry, 1, base, quote, int(x.dir)))
        ev.append((x.t_exit, -1, base, quote, int(x.dir)))
    ev.sort(key=lambda e: (e[0], e[1]))  # exits before entries at the same second
    n = mx = 0
    cur: dict[str, int] = {}
    worst: dict[str, int] = {}
    for _, k, b, q, d in ev:
        n += k
        mx = max(mx, n)
        cur[b] = cur.get(b, 0) + k * d
        cur[q] = cur.get(q, 0) - k * d
        for c in (b, q):
            worst[c] = max(worst.get(c, 0), abs(cur[c]))
    return {"max_concurrent_trades": mx, "max_open_risk_r": mx,
            "max_net_exposure_r_by_currency": dict(sorted(worst.items(), key=lambda kv: -kv[1]))}


def recommend(rep: dict, cross_holm: float | None, name: str) -> tuple[str, str]:
    if not rep.get("trades"):
        return "reject", "no trades"
    net, pf = rep["expectancy_net_r"], rep.get("profit_factor") or 0
    p0 = (rep.get("bootstrap") or {}).get("p_total_le_0", 1)
    dsr = (rep.get("dsr") or {}).get("dsr") or 0
    lr = LONG_RUN.get(name, "none on file")
    if dsr > 0.95 and (cross_holm or 1) < 0.05 and p0 < 0.05:
        return ("retain (research candidate)", "passes DSR/Holm/bootstrap") if not lr.startswith("negative") else \
               ("investigate", f"passes 3m tests but longer run is {lr}")
    if net > 0 and pf >= 1.10 and p0 < 0.25:
        return "investigate", f"positive but not significant (DSR {dsr}, Holm p {cross_holm}, P(total<=0) {p0}); longer run: {lr}"
    return "reject", f"net {net:+.3f}R, PF {pf}, P(total<=0) {p0}; longer run: {lr}"


def best_worst(rep: dict) -> str:
    cells = [(f"{k}: {v}", s["avg_r"], s["n"]) for k in ("by_trend_regime", "by_vol_regime", "by_session")
             for v, s in (rep.get(k) or {}).items() if s["n"] >= 10 and v != "unknown"]
    if not cells:
        return "n < 10 in every cell"
    b, w = max(cells, key=lambda c: c[1]), min(cells, key=lambda c: c[1])
    return f"best {b[0]} ({b[1]:+.2f}R, n{b[2]}); worst {w[0]} ({w[1]:+.2f}R, n{w[2]})"


def selftest() -> None:
    t = pd.DataFrame({"strategy": ["fib618", "imbalance", "enigma", "bennett"], "symbol": ["EURUSD", "EURUSD", "GBPUSD", "EURUSD"],
                      "dir": [1, -1, 1, 1], "t_entry": [100, 150, 160, 300], "t_exit": [200, 250, 400, 350], "r": [1.0, -1, 1, 1]})
    k = one_per_pair(t, LIVE_SET)
    assert list(k.strategy) == ["fib618", "enigma", "bennett"]  # imbalance blocked: EURUSD held 100..200
    e = exposure(k)
    assert e["max_concurrent_trades"] == 2 and e["max_net_exposure_r_by_currency"]["USD"] == 2  # long EUR + long GBP = USD -2
    print("audit_p2_v2 selftest ok")


def main() -> None:
    if "--selftest" in sys.argv:
        return selftest()
    OUT.mkdir(parents=True, exist_ok=True)
    fri = pd.read_csv(SRC / "trades_observed_account.csv")
    held = pd.read_csv(SRC / "trades_observed_weekends_held.csv")
    legacy = pd.read_csv(SRC / "trades_legacy.csv")
    prim = pd.concat([fri[~fri.strategy.isin(WEEKEND_HOLD)], held[held.strategy.isin(WEEKEND_HOLD)]], ignore_index=True)
    prim = prim.assign(profile="v2_live_rules")
    prim.to_csv(OUT / "trades_v2_live_rules.csv", index=False)
    code, trials, srs = registry.code_version(), len(registry.records()) + 1, registry.trial_sharpes()
    reps, pvals, daily = {}, {}, {}
    for n in STRATS:
        g = prim[prim.strategy == n]
        reps[n] = validate.report(g, START, END, trials, srs)
        pvals[n] = validate.p_two_sided((reps[n].get("daily") or {}).get("day_clustered_t"))
        daily[n] = validate.daily_series(g, START, END) if len(g) else pd.Series(dtype=float)
    holm = validate.holm(pvals)
    dm = pd.DataFrame(daily).fillna(0.0)
    corr = dm.corr().round(2)
    pbo = validate.pbo_cscv(dm)
    ports = {"(a) all 8, independent": prim, "(b) live set, independent": prim[prim.strategy.isin(LIVE_SET)],
             "(c) live set, one trade per pair": one_per_pair(prim[prim.strategy.isin(LIVE_SET)], LIVE_SET)}
    prep = {k: validate.report(v, START, END, trials, srs) | {"exposure": exposure(v)} for k, v in ports.items()}
    sens = {"all Friday close": fri, "all weekends held": held, "legacy costs (weekends held, no swap, raw spread + $7)": legacy}
    srows = []
    for sn, df in sens.items():
        for n in STRATS:
            g = df[df.strategy == n]
            r = g.r
            srows.append({"sensitivity": sn, "strategy": n, "trades": len(g), "net_r": round(r.mean(), 3) if len(g) else None,
                          "total_r": round(r.sum(), 1)})
    rows = []
    for n in STRATS:
        r = reps[n]
        rec, why = recommend(r, holm.get(n), n)
        d, b = r.get("daily") or {}, r.get("bootstrap") or {}
        rows.append({"strategy": n, "friday rule": "weekends held" if n in WEEKEND_HOLD else "Fri 20:00 close", "trades": r.get("trades"),
                     "gross R (pre comm+swap)": r.get("gross_r_before_commission_swap"), "net R": r.get("expectancy_net_r"),
                     "swap R": r.get("swap_r"), "total R": r.get("total_r"), "PF": r.get("profit_factor"), "win": r.get("win_rate"),
                     "day t": d.get("day_clustered_t"), "Sharpe ann": d.get("sharpe_ann"), "eff. days": d.get("effective_days"),
                     "PSR": r.get("psr_vs_0"), "DSR": (r.get("dsr") or {}).get("dsr"), "Holm p": holm.get(n),
                     "boot 90% total R": b.get("total_r_ci90"), "P(total<=0)": b.get("p_total_le_0"),
                     "max DD R": (r.get("drawdown") or {}).get("max_dd_r"), "months +": r.get("months_positive"),
                     "recommendation": rec, "why": why, "regimes": best_worst(r)})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT / "strategies.csv", index=False)
    prow = []
    for k, r in prep.items():
        d, b, e = r.get("daily") or {}, r.get("bootstrap") or {}, r["exposure"]
        prow.append({"portfolio": k, "trades": r.get("trades"), "gross R": r.get("gross_r_before_commission_swap"), "net R": r.get("expectancy_net_r"),
                     "total R": r.get("total_r"), "PF": r.get("profit_factor"), "day t": d.get("day_clustered_t"), "Sharpe ann": d.get("sharpe_ann"),
                     "PSR": r.get("psr_vs_0"), "DSR": (r.get("dsr") or {}).get("dsr"), "P(total<=0)": b.get("p_total_le_0"),
                     "boot 90% total R": b.get("total_r_ci90"), "max DD R": (r.get("drawdown") or {}).get("max_dd_r"),
                     "DD recovery d": (r.get("drawdown") or {}).get("max_dd_recovery_days"), "max concurrent": e["max_concurrent_trades"],
                     "max |net| currency R": ", ".join(f"{c} {v}" for c, v in list(e["max_net_exposure_r_by_currency"].items())[:4])})
    ptab = pd.DataFrame(prow)
    ptab.to_csv(OUT / "portfolios.csv", index=False)
    corr.to_csv(OUT / "correlation_daily_net_r.csv")
    (OUT / "detail.json").write_text(json.dumps({"strategies": reps, "portfolios": prep, "holm": holm, "pbo": pbo,
                                                 "correlation": corr.to_dict(), "sensitivity": srows}, indent=1, default=str))
    for k, r in list(reps.items()) + list(prep.items()):
        registry.append({"kind": "audit P2 v2 (live rules per strategy)", "strategy": k, "period": [START, END], "code": code,
                         "command": "python -m bensim.audit_p2_v2", "inputs": [str(SRC / f) for f in ("trades_observed_account.csv", "trades_observed_weekends_held.csv")],
                         "outputs": {"dir": str(OUT)}, "summary": {"trades": r.get("trades"), "avg_r": r.get("expectancy_net_r"),
                                                                    "t_stat": r.get("trade_t"), "profit_factor": r.get("profit_factor"), "total_r": r.get("total_r")}})
    md = ["# P2 v2: 8 strategies, live rules, common dataset", "",
          f"Protocol docs/audit/p2_protocol_v2_2026-11-03.md. Period {START} .. {END} (exclusive), EURUSD GBPUSD USDJPY USDCHF USDCAD XAUUSD, "
          f"costs `observed_account`, code {code}, DSR trials N = {trials}.", "",
          "## Strategies (each run independently)", tab.drop(columns=["why", "regimes"]).to_markdown(index=False), "",
          "### Recommendation reasons and best/worst regime", *[f"- **{x.strategy}**: {x.recommendation}: {x.why}. Regimes: {x.regimes}" for x in tab.itertuples()], "",
          f"**PBO (CSCV, 8 strategies, daily net R):** {pbo}", "",
          "## Portfolios (1R per trade)", ptab.to_markdown(index=False), "",
          "## Correlation of daily net R", corr.to_markdown(), "",
          "## Sensitivities (not used to choose anything)", pd.DataFrame(srows).pivot(index="strategy", columns="sensitivity", values="net_r").to_markdown(), "",
          "## Per-strategy detail", *[validate.to_markdown(n, reps[n]) for n in STRATS],
          "## Portfolio detail", *[validate.to_markdown(k, r) for k, r in prep.items()]]
    (OUT / "REPORT.md").write_text("\n".join(md), encoding="utf-8")
    print(tab.drop(columns=["why", "regimes"]).to_string(index=False))
    print(ptab.to_string(index=False))
    print(corr.to_string())


if __name__ == "__main__":
    main()
