"""Round 21 (2026-11-09): would cost-aware fixed-$ sizing (bensim/sizing.py) have changed the 10-year result?

Same trades, same R (net of costs, from the round-4 histories: FXCM 2012-20 + IC 2021-26), window 2016-11-01..2026-10-31.
  OLD  = today's engine: lots = budget / stop  -> $ = R x budget (the stop loss + costs lands ABOVE the budget)
  NEW  = sizing.size: lots = budget / (stop + spread + 2 x 0.5 pip slip)  -> $ = R x budget x stop / (stop + costs)
Spread = today's live broker median per pair (scratchpad spreads.json). Budgets as live: fib618 $500, Bennett and the
books (imbalance, enigma, sflow) $250. Flow ML: OLD 0.8 fixed lots vs NEW $500 at the 145-bp disaster stop; Flow AM:
OLD 2.5 lots vs NEW $500 at 68 bp. Also R by stop size (post-hoc look, NOT a pre-registered filter test).
    python reports/round21/fixed_risk_10y.py
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI/reports/round4")
from r4_hist import load  # noqa: E402

SPREADS = json.load(open("D:/ClaudeTemp/claude/14620cf7b9ae7a95/4332fb73-f2e2-476e-8c00-71166ba97e2e/scratchpad/spreads.json"))
SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
D = "D:/Devops/trading/OpenTerminalUI/data/"
T0, T1 = pd.Timestamp("2016-11-01"), pd.Timestamp("2026-10-31")
SLIP = 0.5
BUDGET = {"fib618": 500, "bennett": 250, "imbalance": 250, "enigma": 250, "sflow": 250}


def stops() -> dict[str, pd.DataFrame]:
    im = pd.concat([pd.read_csv(SP + "im_out/trades_fxcm.csv"), pd.read_csv(SP + "im_out/trades_ic.csv")], ignore_index=True)
    im["t"] = pd.to_datetime(im.t_entry, unit="s")
    im["stop_px"] = (im.entry - im.sl).abs()
    out = {b: g[["symbol", "t", "r", "stop_px"]] for b, g in im.groupby("book")}
    sf = pd.concat([pd.read_csv(D + "sflow_fxcm_ours_2012_2021.csv"), pd.read_csv(D + "sflow_5y.csv")])
    sf = sf[sf.kind == "A"]
    out["sflow"] = pd.DataFrame({"symbol": sf.symbol, "t": pd.to_datetime(sf.fill_t, unit="s"), "r": sf.r, "stop_px": (sf.entry - sf.stop).abs()})
    return out


def dd(x: np.ndarray) -> float:
    c = np.cumsum(x)
    return float((np.maximum.accumulate(np.maximum(c, 0)) - c).max()) if len(c) else 0.0


def row(name, n, old, new, extra=""):
    return (f"{name:10} {n:5} tr | OLD ${old.sum():+9,.0f}  maxDD ${dd(old):8,.0f} | NEW ${new.sum():+9,.0f}  maxDD ${dd(new):8,.0f} "
            f"| NEW-OLD ${new.sum() - old.sum():+8,.0f}{extra}")


print(f"window {T0.date()} .. {T1.date()}, spread = live median per pair, slippage {SLIP} pip/side\n")
allr = []
for b, g in stops().items():
    g = g[(g.t >= T0) & (g.t <= T1)].dropna(subset=["r", "stop_px"]).sort_values("t")
    g = g[g.stop_px > 0]
    pip = g.symbol.map(lambda s: SPREADS[s]["pip"])
    g = g.assign(stop_pips=g.stop_px / pip, cost_pips=g.symbol.map(lambda s: SPREADS[s]["spread_pips"]) + 2 * SLIP)
    g["f"] = g.stop_pips / (g.stop_pips + g.cost_pips)
    old = (g.r * BUDGET[b]).to_numpy()
    new = (g.r * BUDGET[b] * g.f).to_numpy()
    # same total exposure: scale NEW so its average $-at-risk equals OLD's -> isolates the re-weighting effect
    same = new / g.f.mean()
    print(row(b, len(g), old, new, f" | same-exposure NEW ${same.sum():+8,.0f}  ({g.r.mean():+.3f}R/tr, median stop {g.stop_pips.median():.1f} pips)"))
    allr.append(g.assign(book=b))

a = pd.concat(allr)
a["cost_share"] = a.cost_pips / a.stop_pips
print("\nR per trade by STOP SIZE (all 5 stop-based strategies, net of costs) -- post-hoc look")
a["bucket"] = pd.cut(a.cost_share, [0, 0.05, 0.10, 0.20, 0.35, 10], labels=["costs <5% of stop", "5-10%", "10-20%", "20-35%", ">35%"])
for k, g in a.groupby("bucket", observed=True):
    t = g.r.mean() / g.r.std() * np.sqrt(len(g))
    print(f"  {k:18} {len(g):5} trades  median stop {g.stop_pips.median():6.1f} pips  {g.r.mean():+.3f}R/trade  t {t:+.2f}  "
          f"win {np.mean(g.r > 0):.0%}")
print("\nby strategy x stop size (R/trade, trades)")
print(a.pivot_table(index="book", columns="bucket", values="r", aggfunc=["mean", "count"], observed=True).round(3).to_string())

# Flow ML / AM: OLD fixed lots vs NEW fixed $ at the disaster stop
h = {s: (lambda z: pd.Series(z["c"], index=pd.to_datetime(z["t"], unit="s")))(np.load(D + f"bars/fxcm/{s}_H1.npz")) for s in ("EURUSD", "USDCAD")}
H = load()
print()
for kind, lots, stop_bp in (("flow_ml", 0.8, 145.0), ("flow_am", 2.5, 68.0)):
    f = H[kind]
    f = f[(f.t >= T0) & (f.t <= T1)].sort_values("t")
    eur = h["EURUSD"].reindex(f.t, method="ffill").to_numpy()
    cad = h["USDCAD"].reindex(f.t, method="ffill").to_numpy()
    base_usd = np.where(f.symbol.str[:3] == "EUR", eur, np.where(f.symbol.str[:3] == "CAD", 1 / cad, 1.0))
    old = (f.r / 1e4 * lots * 100_000 * base_usd).to_numpy()
    notional = 500 / (stop_bp / 1e4)  # $ notional so the disaster stop loses $500 (spread/slip ~0.1 bp of 145: ignored)
    new = (f.r / 1e4 * notional).to_numpy()
    same = new * (lots * 100_000 * base_usd).mean() / notional
    print(row(kind, len(f), old, new, f" | same-exposure NEW ${same.sum():+8,.0f}  ({f.r.mean():+.2f} bp/tr; NEW notional ${notional:,.0f}"
              f" vs OLD avg ${(lots * 100_000 * base_usd).mean():,.0f})"))
