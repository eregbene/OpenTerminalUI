"""Audit P2: 8 strategies x 6 symbols x identical execution, frozen params (docs/audit/p2_protocol_2026-11-03.md).

    python -m bensim.audit_p2          # -> reports/experiments/p2_2026-11-03/ + registry entries
Research CPU runs BELOW_NORMAL (live demo first). Writes every trade, every report, and one registry line per run.
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from . import bennett_strategy as bstrat
from . import books, fvg_strategy, gf_strategy, orderflow_strategy, pullback_strategy, registry, validate
from .backtest import exec_profile, run_symbol
from .weekly_check import LIVE as FIB_LIVE

START, END = "2026-08-03", "2026-11-03"
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "XAUUSD"]
STRATS = {"fib618": (FIB_LIVE, "imbalance"), "imbalance": (books.IMB, "imbalance"), "enigma": (books.ENIGMA, "htf_fvg"),
          "h1_fvg": (fvg_strategy.FvgParams(), "htf_fvg"), "bennett": (bstrat.LIVE, "bennett"),
          "pullback": (pullback_strategy.PullbackParams(), "pullback"), "orderflow": (orderflow_strategy.OrderFlowParams(), "orderflow"),
          "gf": (gf_strategy.GfParams(), "gf")}
LIVE_SET = ["fib618", "imbalance", "enigma", "bennett"]
OUT = Path("reports/experiments/p2_2026-11-03")


def profiles() -> dict:
    obs = exec_profile("observed_account")
    return {"observed_account": obs, "legacy": exec_profile("legacy"),
            "observed_weekends_held": replace(obs, friday_close_utc=-1, cost_sources={**obs.cost_sources, "friday_close": "off (weekends held)"})}


def _job(a):
    name, sym, prof, ex = a
    p, kind = STRATS[name]
    t = run_symbol((sym, START, END, p, ex, kind))
    return t.assign(strategy=name, profile=prof) if not t.empty else t


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # children inherit
    except Exception:
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    code = registry.code_version()
    data = registry.data_fingerprint(SYMS, START, END)
    profs = profiles()
    jobs = [(n, s, pn, ex) for pn, ex in profs.items() for n in STRATS for s in SYMS]
    with ProcessPoolExecutor(3) as pool:
        frames = [f for f in pool.map(_job, jobs) if not f.empty]
    tr = pd.concat(frames, ignore_index=True)
    trials, srs = len(registry.records()) + len(STRATS) * len(profs), registry.trial_sharpes()  # this run's trials count too
    summary_rows, md = [], ["# P2: 8 strategies, common dataset, frozen params", "",
                            f"Period {START} .. {END} (end exclusive), symbols {', '.join(SYMS)}, code {code}, DSR trials N = {trials}.",
                            "Protocol: docs/audit/p2_protocol_2026-11-03.md. Costs per profile below.", ""]
    for pn, ex in profs.items():
        t = tr[tr.profile == pn]
        t.to_csv(OUT / f"trades_{pn}.csv", index=False)
        md += [f"## Profile `{pn}`", "Cost sources: " + "; ".join(f"**{k}**: {v}" for k, v in ex.cost_sources.items()), ""]
        reps, pvals, daily = {}, {}, {}
        sets = [(n, t[t.strategy == n]) for n in STRATS] + [("PORTFOLIO all 8", t), ("PORTFOLIO live set", t[t.strategy.isin(LIVE_SET)])]
        for n, g in sets:
            rep = validate.report(g, START, END, trials, srs, regimes=(pn == "observed_account"))
            reps[n] = rep
            (OUT / f"{pn}__{n.replace(' ', '_')}.json").write_text(json.dumps(rep, indent=1, default=str))
            md.append(validate.to_markdown(n, rep))
            if n in STRATS:
                pvals[n] = validate.p_two_sided((rep.get("daily") or {}).get("day_clustered_t"))
                daily[n] = validate.daily_series(g, START, END) if len(g) else None
            registry.append({"kind": "audit P2 run", "strategy": n, "profile": pn, "params": asdict(STRATS[n][0]) if n in STRATS else LIVE_SET,
                             "exec": asdict(ex), "symbols": SYMS, "period": [START, END], "code": code, "data": data,
                             "command": "python -m bensim.audit_p2", "outputs": {"trades": str(OUT / f"trades_{pn}.csv"),
                                                                                 "report": str(OUT / f"{pn}__{n.replace(' ', '_')}.json")},
                             "summary": {"trades": rep.get("trades"), "avg_r": rep.get("expectancy_net_r"), "t_stat": rep.get("trade_t"),
                                         "profit_factor": rep.get("profit_factor"), "total_r": rep.get("total_r")}})
            summary_rows.append({"profile": pn, "set": n, "trades": rep.get("trades"), "net_r": rep.get("expectancy_net_r"),
                                 "gross_before_comm_swap": rep.get("gross_r_before_commission_swap"), "total_r": rep.get("total_r"),
                                 "pf": rep.get("profit_factor"), "day_t": (rep.get("daily") or {}).get("day_clustered_t"),
                                 "sharpe_ann": (rep.get("daily") or {}).get("sharpe_ann"), "psr": rep.get("psr_vs_0"),
                                 "dsr": (rep.get("dsr") or {}).get("dsr"), "p_boot_le0": (rep.get("bootstrap") or {}).get("p_total_le_0"),
                                 "max_dd_r": (rep.get("drawdown") or {}).get("max_dd_r"), "months_pos": rep.get("months_positive")})
        holm = validate.holm(pvals)
        dm = pd.DataFrame({k: v for k, v in daily.items() if v is not None}).fillna(0.0)
        pbo = validate.pbo_cscv(dm)
        md += [f"**Holm-adjusted p (day-clustered t, 8 strategies):** " + ", ".join(f"{k} {v}" for k, v in holm.items()),
               f"**PBO (CSCV over the 8 strategies' daily R):** {pbo}", ""]
        for row in summary_rows:
            if row["profile"] == pn and row["set"] in holm:
                row["holm_p"] = holm[row["set"]]
        (OUT / f"{pn}__cross.json").write_text(json.dumps({"holm": holm, "pbo": pbo}, indent=1))
    s = pd.DataFrame(summary_rows)
    s.to_csv(OUT / "summary.csv", index=False)
    md = md[:5] + ["## Summary table", s.to_markdown(index=False), ""] + md[5:]
    (OUT / "REPORT.md").write_text("\n".join(md), encoding="utf-8")
    print(s.to_string(index=False))


if __name__ == "__main__":
    main()
