"""R1: is fib618 / enigma profit concentrated around high-impact news? (docs/audit/r1_r2_preregistration_2026-11-03.md)

    python -m bensim.r1_news --split validation        # then, once, after the validation report is frozen:
    python -m bensim.r1_news --split confirmation
Labels come from entry time + calendar only and are saved BEFORE outcomes are joined.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, registry, validate
from .backtest import CURRENCIES

DIR = Path("reports/experiments/r12_2026-11-03")
STRATS = ["fib618", "enigma"]
H24 = 24 * 3600
SEED, PATHS = 19, 10_000


def events() -> dict[str, np.ndarray]:
    with db.connect() as c:
        rows = c.execute("select extract(epoch from ts)::bigint, currency from economic_events where impact='high' order by ts").fetchall()
    ev: dict[str, list] = {}
    for t, cur in rows:
        ev.setdefault(cur, []).append(t)
    return {k: np.array(v, dtype=np.int64) for k, v in ev.items()}


def _any(ev: dict, curs: list[str], a: np.ndarray, b: np.ndarray, shift: int = 0) -> np.ndarray:
    """True where some event of `curs` (or 'All') lies in (a, b]."""
    out = np.zeros(len(a), bool)
    for c in curs + ["All"]:
        x = ev.get(c)
        if x is None:
            continue
        x = x + shift
        out |= np.searchsorted(x, b, side="right") > np.searchsorted(x, a, side="right")
    return out


def pair_currencies(sym: str) -> list[str]:
    base, quote = CURRENCIES[sym]
    return [c for c in (base, quote) if c != "XAU"]


def labels(t: pd.DataFrame, ev: dict, shift: int = 0) -> pd.DataFrame:
    out = []
    for sym, g in t.groupby("symbol"):
        a, b = g.t_entry.to_numpy(), g.t_entry.to_numpy() + H24
        curs = pair_currencies(sym)
        other = [c for c in curs if c not in ("USD", "EUR")]
        out.append(pd.DataFrame({"news24": _any(ev, curs, a, b, shift), "usd24": _any(ev, [c for c in curs if c == "USD"], a, b, shift),
                                 "eur24": _any(ev, [c for c in curs if c == "EUR"], a, b, shift),
                                 "other24": _any(ev, other, a, b, shift) if other else np.zeros(len(a), bool)}, index=g.index))
    return pd.concat(out).loc[t.index]


def descriptive(t: pd.DataFrame, ev: dict) -> pd.DataFrame:
    """Outcome-dependent labels (never tested): an event while held; exit within 30 min after an event."""
    out = []
    for sym, g in t.groupby("symbol"):
        curs = pair_currencies(sym)
        out.append(pd.DataFrame({"news_held": _any(ev, curs, g.t_entry.to_numpy(), g.t_exit.to_numpy()),
                                 "exit_at_news": _any(ev, curs, g.t_exit.to_numpy() - 1800, g.t_exit.to_numpy())}, index=g.index))
    return pd.concat(out).loc[t.index]


def week_boot_diff(t: pd.DataFrame, flag: str, paths: int = PATHS, seed: int = SEED) -> dict:
    """Mean R(flag) - mean R(not flag), resampling whole calendar weeks of trades."""
    wk = (t.t_entry // (7 * 86400)).to_numpy()
    uw, inv = np.unique(wk, return_inverse=True)
    f = t[flag].to_numpy()
    r = t.r.to_numpy()
    s1, n1 = np.bincount(inv, r * f, len(uw)), np.bincount(inv, f.astype(float), len(uw))
    s0, n0 = np.bincount(inv, r * ~f, len(uw)), np.bincount(inv, (~f).astype(float), len(uw))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uw), (paths, len(uw)))
    c1, k1, c0, k0 = s1[idx].sum(1), n1[idx].sum(1), s0[idx].sum(1), n0[idx].sum(1)
    ok = (k1 > 0) & (k0 > 0)
    d = c1[ok] / k1[ok] - c0[ok] / k0[ok]
    obs = r[f].mean() - r[~f].mean() if f.any() and (~f).any() else float("nan")
    return {"diff": round(float(obs), 3), "ci95": [round(float(np.quantile(d, q)), 3) for q in (0.025, 0.975)],
            "p_one_sided": round(float((d <= 0).mean()), 4), "weeks": int(len(uw))}


def grp(t: pd.DataFrame, by) -> dict:
    return {str(k): {"n": int(len(g)), "avg_r": round(float(g.r.mean()), 3), "total_r": round(float(g.r.sum()), 1),
                     "pf": round(float(g.r[g.r > 0].sum() / -g.r[g.r < 0].sum()), 2) if (g.r < 0).any() else None}
            for k, g in t.groupby(by)}


def concentration(t: pd.DataFrame, ev: dict) -> dict:
    """H1b: share of top-decile-R trades with news24 vs all trades; null = calendar shifted by k whole weeks."""
    top = t.r >= t.r.quantile(0.9)
    real = float(t.news24[top].mean() - t.news24.mean())
    null = []
    for k in [k for k in range(-26, 27) if k]:
        lab = labels(t, ev, shift=k * 7 * 86400)
        null.append(float(lab.news24[top].mean() - lab.news24.mean()))
    return {"top_decile_n": int(top.sum()), "news24_share_top": round(float(t.news24[top].mean()), 3),
            "news24_share_all": round(float(t.news24.mean()), 3), "excess": round(real, 3),
            "p_one_sided_vs_52_week_shifts": round(float(np.mean([x >= real for x in null])), 3)}


def analyse(t: pd.DataFrame, ev: dict) -> dict:
    t = t.sort_values("t_exit").reset_index(drop=True)
    out = {"trades": int(len(t)), "all": grp(t, lambda i: "all"), "news24": grp(t, "news24"),
           "h1_news24_minus_none": week_boot_diff(t, "news24"),
           "by_currency_flag": {f: grp(t, f) | {"diff_boot": week_boot_diff(t, f)} for f in ("usd24", "eur24", "other24") if t[f].any()},
           "h1b_concentration": concentration(t, ev),
           "by_year": {str(y): grp(g, "news24") for y, g in t.groupby(pd.to_datetime(t.t_entry, unit="s").dt.year)},
           "by_pair": {s: grp(g, "news24") for s, g in t.groupby("symbol")},
           "descriptive_outcome_dependent": {"news_held": grp(t, "news_held"), "exit_at_news": grp(t, "exit_at_news")}}
    top1 = t.r >= t.r.quantile(0.99)
    top5 = t.index.isin(t.r.nlargest(5).index)
    for name, keep in (("without_top_1pct", ~top1), ("without_top_5", ~top5)):
        u = t[keep]
        out[name] = {"news24": grp(u, "news24"), "h1": week_boot_diff(u, "news24"), "all": grp(u, lambda i: "all")}
    out["top5_trades"] = t.loc[top5, ["symbol", "dir", "t_entry", "r", "news24", "usd24", "eur24", "other24", "news_held"]].assign(
        t_entry=lambda x: pd.to_datetime(x.t_entry, unit="s", utc=True).astype(str)).to_dict("records")
    out["validate_all"] = validate.report(t, regimes=False)
    return out


def md(split: str, res: dict, holm: dict) -> str:
    L = [f"# R1 news exposure: {split}", "",
         "Labels from entry time + calendar only (high impact, (entry, entry+24h]); outcomes joined afterwards. "
         "Week-block bootstrap 10,000 / seed 19. Pre-registration: docs/audit/r1_r2_preregistration_2026-11-03.md", ""]
    for s, r in res.items():
        h = r["h1_news24_minus_none"]
        L += [f"## {s} ({r['trades']} trades)",
              f"- all: {r['all']['all']}",
              f"- news24 groups: {r['news24']}",
              f"- **H1 diff (news - none) {h['diff']:+.3f}R, 95% CI {h['ci95']}, one-sided p {h['p_one_sided']}, Holm p {holm.get(s)}** ({h['weeks']} weeks)",
              f"- H1b concentration: {r['h1b_concentration']}",
              f"- without top 1%: {r['without_top_1pct']['all']['all']}; groups {r['without_top_1pct']['news24']}; H1 {r['without_top_1pct']['h1']}",
              f"- without top 5: {r['without_top_5']['all']['all']}; groups {r['without_top_5']['news24']}; H1 {r['without_top_5']['h1']}",
              "- by currency flag: " + "; ".join(f"{f}: {v['True'] if 'True' in v else '-'} vs {v.get('False')} diff {v['diff_boot']['diff']:+.3f} p {v['diff_boot']['p_one_sided']}" for f, v in r["by_currency_flag"].items()),
              "- by year (news24 False/True): " + "; ".join(f"{y}: " + ", ".join(f"{k} n{v['n']} {v['avg_r']:+.2f}" for k, v in g.items()) for y, g in r["by_year"].items()),
              "- by pair (news24 False/True): " + "; ".join(f"{p}: " + ", ".join(f"{k} n{v['n']} {v['avg_r']:+.2f}" for k, v in g.items()) for p, g in r["by_pair"].items()),
              f"- descriptive (outcome-dependent, not tested): {r['descriptive_outcome_dependent']}",
              f"- top 5 trades: {r['top5_trades']}",
              f"- overall validation stats: DSR {r['validate_all']['dsr']['dsr']}, PSR {r['validate_all']['psr_vs_0']}, bootstrap {r['validate_all']['bootstrap']}", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["validation", "confirmation"], required=True)
    a = ap.parse_args()
    t = pd.read_csv(DIR / f"trades_{a.split}.csv")
    t = t[t.strategy.isin(STRATS)].reset_index(drop=True)
    ev = events()
    lab = labels(t, ev).join(descriptive(t, ev))
    lab.assign(strategy=t.strategy, symbol=t.symbol, t_entry=t.t_entry).to_csv(DIR / f"r1_labels_{a.split}.csv", index=False)  # before outcomes
    t = t.join(lab)
    res = {s: analyse(t[t.strategy == s].copy(), ev) for s in STRATS}
    holm = validate.holm({s: r["h1_news24_minus_none"]["p_one_sided"] for s, r in res.items()})
    (DIR / f"R1_{a.split}.json").write_text(json.dumps({"results": res, "holm": holm}, indent=1, default=str))
    (DIR / f"R1_{a.split}.md").write_text(md(a.split, res, holm), encoding="utf-8")
    registry.append({"kind": f"R1 news exposure ({a.split})", "strategy": STRATS, "code": registry.code_version(),
                     "command": f"python -m bensim.r1_news --split {a.split}", "outputs": {"report": str(DIR / f"R1_{a.split}.md")},
                     "summary": {s: {"trades": r["trades"], "h1": r["h1_news24_minus_none"]} for s, r in res.items()} | {"holm": holm}})
    print(md(a.split, res, holm))


if __name__ == "__main__":
    main()
