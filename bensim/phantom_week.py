"""Phantom report for a period: every trade of the chosen entry/exit, a chart per trade and an HTML page.

    python -m bensim.phantom_week --csv data/phantom_recent.csv --from 2026-10-05 --to 2026-10-09 [--path conf_mid --exit partial]
Chart: M15 candles, the 4H/1H zone (orange), the lower-TF order-block entry, stop and the previous-high target; the
lower panel is the 5M around the tap and the fill. Output: docs/charts/phantom/<from>_<to>/index.html
"""
from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .backtest import load_m5, resample  # noqa: E402

USD_R = 488.4  # 0.5% of the 97,677 account


def _candles(ax, b: dict, a: int, z: int) -> None:
    for i in range(a, z):
        col = "#26a69a" if b["close"][i] >= b["open"][i] else "#ef5350"
        ax.plot([i, i], [b["low"][i], b["high"][i]], color=col, lw=0.7)
        ax.plot([i, i], [b["open"][i], b["close"][i]], color=col, lw=2.6)


def chart(r: pd.Series, path: str, ex: str, out: Path) -> Path:
    m5 = load_m5(r.symbol, pd.Timestamp(int(r.created) - 3 * 86400, unit="s", tz="UTC").isoformat(),
                 pd.Timestamp(int(r[f"{path}_fill_t"]) + 3 * 86400, unit="s", tz="UTC").isoformat())
    m15 = resample(m5, 900)
    ny = lambda t: pd.Timestamp(int(t), unit="s").strftime("%a %d %H:%M")  # noqa: E731
    fig, ax = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 2]})
    a = max(0, int(np.searchsorted(m15["t"], int(r.created) - 12 * 3600)))
    z = min(len(m15["t"]), int(np.searchsorted(m15["t"], int(r[f"{path}_fill_t"]) + 30 * 3600)))
    _candles(ax[0], m15, a, z)
    ent, stp, tgt = r[f"{path}_entry"], r[f"{path}_stop"], r.target
    ax[0].axhspan(r.zone_bot, r.zone_top, color="orange", alpha=0.18, label=f"{r.zone_tf} zone {r.zone_bot:.5g}-{r.zone_top:.5g} ({r.model})")
    for y, c, lab in ((ent, "black", f"entry {ent:.5g}"), (stp, "red", f"stop {stp:.5g} ({r[f'{path}_stop_pips']:.1f} pips)"),
                      (tgt, "green", f"previous high/low target {tgt:.5g}")):
        ax[0].axhline(y, color=c, lw=1, ls="--", label=lab)
    for t, c, lab in ((r.created, "purple", "zone created"), (r.touch_t, "blue", "tap"), (r[f"{path}_fill_t"], "black", "fill")):
        ax[0].axvline(int(np.searchsorted(m15["t"], int(t))), color=c, lw=0.8, ls=":", label=f"{lab} {ny(t)}")
    res = r[f"{path}_{ex}_r"]
    side = "BUY" if r.dir == 1 else "SELL"
    ax[0].set_title(f"{r.symbol} {side} -- Phantom {path.replace('_', ' ')} / {ex}: {res:+.2f}R (${res * USD_R:+,.0f})  [M15, UTC]")
    ax[0].legend(fontsize=8, loc="best")
    a5 = int(np.searchsorted(m5["t"], int(r.touch_t) - 2 * 3600))
    z5 = int(np.searchsorted(m5["t"], int(r[f"{path}_fill_t"]) + 8 * 3600))
    _candles(ax[1], m5, a5, z5)
    ax[1].axhspan(r.zone_bot, r.zone_top, color="orange", alpha=0.12)
    ax[1].axhline(ent, color="black", lw=1, ls="--")
    ax[1].axhline(stp, color="red", lw=1, ls="--")
    ax[1].axvline(int(np.searchsorted(m5["t"], int(r.touch_t))), color="blue", lw=0.8, ls=":")
    ax[1].axvline(int(np.searchsorted(m5["t"], int(r[f"{path}_fill_t"]))), color="black", lw=0.8, ls=":")
    ax[1].set_title("M5: tap of the zone (blue) -> lower-TF break of structure -> limit on the new order block (black = fill)")
    plt.tight_layout()
    f = out / f"{r.symbol}_{pd.Timestamp(int(r[f'{path}_fill_t']), unit='s'):%m%d_%H%M}.png"
    plt.savefig(f, dpi=72)
    plt.close(fig)
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/phantom_recent.csv")
    ap.add_argument("--from", dest="a", required=True)
    ap.add_argument("--to", dest="b", required=True)
    ap.add_argument("--path", default="conf_mid", help="conf_mid (50%% entry) | conf_top | twostep_mid | twostep_top")
    ap.add_argument("--exit", dest="ex", default="partial", help="partial (90%% at previous high) | 2R | 3R | 5R | prevhigh | trail")
    ap.add_argument("--charts", type=int, default=40, help="max charts")
    a = ap.parse_args()
    d = pd.read_csv(a.csv)
    t0, t1 = pd.Timestamp(a.a, tz="UTC").timestamp(), (pd.Timestamp(a.b, tz="UTC") + pd.Timedelta(days=1)).timestamp()
    col = f"{a.path}_{a.ex}_r"
    g = d[(d[f"{a.path}_fill_t"] >= t0) & (d[f"{a.path}_fill_t"] < t1) & d[col].notna()].sort_values(f"{a.path}_fill_t")
    out = Path("docs/charts/phantom") / f"{a.a}_{a.b}"
    out.mkdir(parents=True, exist_ok=True)
    pics = [chart(r, a.path, a.ex, out) for _, r in g.head(a.charts).iterrows()]
    rows = "".join(f"<tr><td>{r.symbol}</td><td>{'BUY' if r.dir == 1 else 'SELL'}</td><td>{pd.Timestamp(int(r[f'{a.path}_fill_t']), unit='s'):%a %d %b %H:%M}</td>"
                   f"<td>{r.zone_tf} {r.model}</td><td>{r[f'{a.path}_entry']:.5g}</td><td>{r[f'{a.path}_stop']:.5g}</td><td>{r[f'{a.path}_stop_pips']:.1f}</td>"
                   f"<td>{r.target:.5g}</td><td class='{'w' if r[col] > 0 else 'l'}'>{r[col]:+.2f}R</td><td class='{'w' if r[col] > 0 else 'l'}'>${r[col] * USD_R:+,.0f}</td></tr>"
                   for _, r in g.iterrows())
    tot = g[col].sum()
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phantom {a.a} - {a.b}</title><style>body{{font-family:Segoe UI,Arial;margin:16px;max-width:1400px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:3px 7px;font-size:14px}}.w{{color:#2e7d32;font-weight:bold}}.l{{color:#c62828;font-weight:bold}}img{{width:100%;margin:14px 0;border:1px solid #ddd}}</style></head><body>
<h1>Phantom -- {a.a} to {a.b}</h1><p>Entry: {a.path} ({'50% of the order block' if 'mid' in a.path else 'top of the order block'}), exit: {a.ex};
3-pip minimum stop; 1R = 0.5% = ${USD_R:,.0f}; R net of spread + commission. <b>{len(g)} trades, {tot:+.1f}R = ${tot * USD_R:+,.0f}</b>
(win rate {100 * (g[col] > 0).mean():.0f}%). Backtest replay, no lookahead -- not live trades.</p>
<table><tr><th>Pair</th><th>Side</th><th>Fill (UTC)</th><th>Zone</th><th>Entry</th><th>Stop</th><th>Stop pips</th><th>Target</th><th>R</th><th>$</th></tr>{rows}</table>
{''.join(f"<img src='{p.name}'>" for p in pics)}</body></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"{len(g)} trades, {tot:+.1f}R = ${tot * USD_R:+,.0f} -> {(out / 'index.html').resolve()}")
    webbrowser.open((out / "index.html").resolve().as_uri())


if __name__ == "__main__":
    main()
