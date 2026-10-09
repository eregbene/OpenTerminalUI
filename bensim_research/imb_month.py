"""Run the imbalance-video strategy on one recent month using the live demo feed, and chart every trade.

Base = FOREXSB M15 (UTC, to 2026-08-14) + ICMarketsSC-Demo M5 rebuilt into M15 after that (verified UTC:
median close difference 0.1 pip vs FOREXSB on the 12-14 Aug overlap).

python -m bensim_research.imb_month --month 2026-09
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from . import data as D
from . import imb_wf as W

CORE_FILTER = "no M30, stop > 8 spreads, no GBPUSD, no entry 12-20 UTC, skip HTF-aligned"


def live_base(symbol: str) -> D.Bars:
    hist = D.load(symbol, "M15")[0]
    m5, _ = D.clean(D._fetch(symbol, "M5", "MT5", "ICMarketsSC-Demo"), "M5")
    new = D.resample(m5, "M15")
    keep = new.t > hist.t[-1]
    cols = [np.r_[getattr(hist, k), getattr(new, k)[keep]] for k in "tohlcv"]
    return D.Bars("M15", *cols)


def chart(r, b: D.Bars, path) -> None:
    i_base = int(np.searchsorted(b.t, r.t_base))
    i_e = int(np.searchsorted(b.t, r.t_entry, side="right")) - 1
    i_x = int(np.searchsorted(b.t, r.t_exit, side="right")) - 1
    lo, hi = max(0, i_base - 25), min(len(b), max(i_x, i_e) + 15)
    hi = min(hi, lo + 160)
    lv = {"entry": r.entry, "stop": r.stop, "target": r.target}
    if not np.isnan(r.level):
        lv["BOS level"] = r.level
    ymin = min(b.l[lo:hi].min(), *lv.values(), r.zone_lo)
    ymax = max(b.h[lo:hi].max(), *lv.values(), r.zone_hi)
    pad = (ymax - ymin) * 0.05
    ymin, ymax = ymin - pad, ymax + pad
    W_, H_, L, T, R, B = 1100, 600, 10, 70, 110, 20
    im = Image.new("RGB", (W_, H_), "white")
    g = ImageDraw.Draw(im, "RGBA")
    font = ImageFont.load_default(size=13)
    cw = (W_ - L - R) / (hi - lo)
    X = lambda i: L + (i - lo + 0.5) * cw  # noqa: E731
    Y = lambda p: T + (ymax - p) / (ymax - ymin) * (H_ - T - B)  # noqa: E731
    col = (34, 197, 94, 70) if r.dir == 1 else (239, 68, 68, 70)
    g.rectangle([X(i_base) - cw / 2, Y(r.zone_hi), X(min(i_e, hi - 1)) + cw / 2, Y(r.zone_lo)], fill=col)
    for i in range(lo, hi):
        c = (22, 163, 74) if b.c[i] >= b.o[i] else (220, 38, 38)
        g.line([X(i), Y(b.h[i]), X(i), Y(b.l[i])], fill=c)
        y0, y1 = sorted((Y(b.o[i]), Y(b.c[i])))
        g.rectangle([X(i) - cw * 0.35, y0, X(i) + cw * 0.35, max(y1, y0 + 1)], fill=c)
    styles = {"entry": (0, 0, 0), "stop": (220, 38, 38), "target": (22, 163, 74), "BOS level": (37, 99, 235)}
    for k, v in lv.items():
        x0 = X(i_base) if k == "BOS level" else X(i_e)
        g.line([x0, Y(v), W_ - R, Y(v)], fill=styles[k], width=1 if k == "BOS level" else 2)
        g.text((W_ - R + 4, Y(v) - 7), k, fill=styles[k], font=font)
    g.ellipse([X(i_e) - 5, Y(r.entry) - 5, X(i_e) + 5, Y(r.entry) + 5], outline=(0, 0, 0), width=2)
    g.ellipse([X(i_x) - 5, Y(r.exit_px) - 5, X(i_x) + 5, Y(r.exit_px) + 5], outline=(124, 58, 237), width=2)
    when = pd.Timestamp(int(r.t_entry), unit="s").strftime("%Y-%m-%d %H:%M UTC")
    head = [f"{r.symbol} {r.tf} {'BUY' if r.dir == 1 else 'SELL'}  entry {when}  zone {r.zone_lo:.5g}-{r.zone_hi:.5g}  "
            f"entry {r.entry:.5g} stop {r.stop:.5g} target {r.target:.5g} (R:R {r.rr:.1f})",
            f"result: {r.exit_reason.upper()}  {r.r:+.2f}R net  | passes filter: {r.passes}  | HTF aligned {bool(r.htf)}",
            "shaded = zone (base candle of the imbalance run), blue = swing broken (BOS), circle = entry, purple circle = exit"]
    for k, s in enumerate(head):
        g.text((L, 6 + 20 * k), s, fill=(17, 24, 39), font=font)
    im.save(path, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-09")
    args = ap.parse_args()
    m0 = pd.Timestamp(args.month + "-01", tz="UTC")
    m1 = m0 + pd.offsets.MonthBegin(1)
    dest = W.DEST / f"month_{args.month}"
    dest.mkdir(parents=True, exist_ok=True)
    rows, bases = [], {}
    for sym in D.SYMBOLS:
        base = live_base(sym)
        bases[sym] = base
        p = W.pool(sym, base)
        p = p[(p.tmode == "near") & (p.t_entry >= m0.timestamp()) & (p.t_entry < m1.timestamp())]
        p["open_at_data_end"] = p.t_exit >= int(base.t[-1])
        p["data_hole"] = D.overlaps_hole(D.holes(base), p.t_entry.to_numpy(), p.t_exit.to_numpy())
        rows.append(p)
    t = pd.concat(rows, ignore_index=True).sort_values("t_entry")
    hour = (t.t_entry % 86400) // 3600
    t["rr"] = (t.target - t.entry) * t.dir / ((t.entry - t.stop) * t.dir)
    t["passes"] = (t.tf != "M30") & (t.minmult_val > 8) & (t.symbol != "GBPUSD") & ~hour.between(12, 19) & (t.htf == 0)
    t.to_csv(dest / "trades.csv", index=False)
    for i, r in enumerate(t.itertuples(index=False)):
        b = D.resample(bases[r.symbol], r.tf)
        chart(r, b, dest / f"{i:03d}_{r.symbol}_{r.tf}_{'pass' if r.passes else 'fail'}_{r.exit_reason}.png")
    print(f"excluded: {int(t.data_hole.sum())} trades overlap a missing-data hole (stop may have been hit unseen)")
    done = t[~t.open_at_data_end & ~t.data_hole]
    for name, g in (("video rules (all)", done), (f"filtered ({CORE_FILTER})", done[done.passes])):
        print(f"\n{name}: trades {len(g)}, wins {int((g.exit_reason == 'target').sum())}, net {g.r.sum():+.1f}R, avg {g.r.mean():+.3f}R")
        print(g.groupby("tf").r.agg(["count", "sum", "mean"]).round(2).to_string())
    print(f"\nstill open at data end (3 Oct): {int(t.open_at_data_end.sum())}; charts in {dest}")


if __name__ == "__main__":
    main()
