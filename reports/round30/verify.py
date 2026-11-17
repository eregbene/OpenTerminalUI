"""Hand-check 10 trades of the round 30 textbook model (H4 OTE -> M15 MSS -> M15 FVG 50% -> stop under the M15 low -> H4 high).
Re-derives every step with SEPARATE, plain-loop code (not topdown.py's functions) from the raw M5 bars, prints the
step-by-step story of each trade and draws a chart per trade (reports/round30/check_XX.png, real prices).
    python reports/round30/verify.py
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round30")
OUT = Path(ROOT + "/reports/round30")
SYM = "GBPUSD"


def bars_tf(m, sec):
    """Plain resample: group M5 bars by floor(t / sec)."""
    df = pd.DataFrame(m)
    df["k"] = df.t // sec
    g = df.groupby("k")
    return pd.DataFrame({"t": g.t.first() // sec * sec, "open": g.open.first(), "high": g.high.max(),
                         "low": g.low.min(), "close": g.close.last()}).reset_index(drop=True)


def is_pivot(x, i, high):
    """2-bar fractal by hand."""
    if i < 2 or i + 2 >= len(x):
        return False
    if high:
        return x[i] >= x[i - 1] and x[i] >= x[i - 2] and x[i] > x[i + 1] and x[i] > x[i + 2]
    return x[i] <= x[i - 1] and x[i] <= x[i - 2] and x[i] < x[i + 1] and x[i] < x[i + 2]


def last_confirmed(x, upto, high):
    """Latest fractal p with both right bars closed by bar `upto` (p + 2 <= upto)."""
    for p in range(upto - 2, 1, -1):
        if is_pivot(x, p, high):
            return p
    return None


def check(tr, m, H4, M15):
    t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
    notes, ok = [], True
    # 1) H4 BOS bar closes at t_bos
    j = int(np.flatnonzero(H4.t.to_numpy() + 14400 == tr.t_bos)[0])
    ph = last_confirmed(H4.high.to_numpy(), j - 1, True)
    bos = H4.close[j] > H4.high[ph]
    pl = last_confirmed(H4.low.to_numpy(), j, False)
    ok &= bos and abs(H4.low[pl] - tr.range_low) < 1e-9
    notes.append(f"H4 BOS: close {abs(H4.close[j]):.5f} beyond swing {abs(H4.high[ph]):.5f} -> {bos}; range low (swing) {abs(H4.low[pl]):.5f} "
                 f"= code {abs(tr.range_low):.5f}")
    # 2) zone touch: first M5 bar after the BOS whose low reaches 0.62 of (running high - range low)
    z0 = int(np.searchsorted(t, tr.t_bos))
    run = H4.high[pl:j + 1].max()
    q = None
    for k in range(z0, len(t)):
        if lo[k] <= run - 0.62 * (run - tr.range_low):
            q = k
            break
        run = max(run, hi[k])
    ok &= t[q] == tr.t_zone and abs(run - tr.range_high) < 1e-9
    notes.append(f"OTE 0.62 touch at {pd.to_datetime(t[q], unit='s')} (code {pd.to_datetime(tr.t_zone, unit='s')}), "
                 f"range high {abs(run):.5f} (code {abs(tr.range_high):.5f}), OTE level {abs(run - 0.62 * (run - tr.range_low)):.5f}")
    # 3) M15 MSS: lowest low since the touch, close above the last confirmed M15 swing high before that low
    js = int(np.searchsorted(M15.t.to_numpy(), t[q], "right") - 1)
    lowv, low_i, mss = M15.low[js], js, None
    for jj in range(js, len(M15)):
        if M15.low[jj] < lowv:
            lowv, low_i = M15.low[jj], jj
        p = None
        for pp in range(min(low_i - 1, jj - 2), 1, -1):
            if is_pivot(M15.high.to_numpy(), pp, True):
                p = pp
                break
        if p is not None and M15.close[jj] > M15.high[p]:
            mss = jj
            break
    ok &= mss is not None and M15.t[mss] + 900 == tr.t_mss and abs(lowv - tr.ltf_low) < 1e-9
    notes.append(f"M15 MSS close at {pd.to_datetime(M15.t[mss] + 900, unit='s')} above swing {abs(M15.high[p]):.5f}; M15 low in the zone "
                 f"{abs(lowv):.5f} (code {abs(tr.ltf_low):.5f})")
    # 4) newest M15 FVG in the MSS leg, entry at its 50%
    fv = [(M15.high[x - 2], M15.low[x]) for x in range(low_i + 2, mss + 1) if M15.low[x] > M15.high[x - 2]]
    ce = (fv[-1][0] + fv[-1][1]) / 2
    ok &= abs(ce - tr.px) < 1e-9
    notes.append(f"M15 FVG {abs(fv[-1][0]):.5f}-{abs(fv[-1][1]):.5f}, entry at 50% {abs(ce):.5f} (code {abs(tr.px):.5f}); "
                 f"stop {abs(tr.sl):.5f}, target {abs(tr.tp):.5f}")
    # 5) fill + outcome walked bar by bar
    zm = int(np.searchsorted(t, M15.t[mss] + 900))
    f = next(k for k in range(zm, len(t)) if lo[k] <= ce)
    ok &= t[f] == tr.t_entry
    res, k = None, f
    end = int(np.searchsorted(t, tr.t_end, "right"))
    while k < end:
        if lo[k] <= tr.sl:
            res = -1.0
            break
        if hi[k] >= tr.tp:
            res = (tr.tp - ce) / (ce - tr.sl)
            break
        k += 1
    if res is None:
        res = (cl[end - 1] - ce) / (ce - tr.sl)
    ok &= abs(res - tr.cost_R - tr.R) < 1e-6
    notes.append(f"filled {pd.to_datetime(t[f], unit='s')}; {'STOP' if res == -1 else 'TARGET' if k < end else 'TIME'} at "
                 f"{pd.to_datetime(t[min(k, end - 1)], unit='s')}: {res:+.2f}R - costs {tr.cost_R:.2f}R = {res - tr.cost_R:+.2f}R (code {tr.R:+.2f}R)")
    return ok, notes, (z0, k, q, f, mss)


def chart(i, tr, m, M15, idx):
    z0, k, q, f, mss = idx
    s = -1 if tr.dir == -1 else 1
    t0, t1 = tr.t_bos - 3 * 86400, m["t"][min(k, len(m["t"]) - 1)] + 86400
    w = M15[(M15.t >= t0) & (M15.t <= t1)]
    fig, ax = plt.subplots(figsize=(13, 6))
    for _, b in w.iterrows():
        o, h, l_, c = (s * b.open, s * b.high, s * b.low, s * b.close) if s == 1 else (-b.open, -b.low, -b.high, -b.close)
        x = pd.to_datetime(b.t, unit="s")
        ax.vlines(x, l_, h, color="#555", lw=0.6)
        ax.vlines(x, min(o, c), max(o, c), color="#2e7d32" if c >= o else "#c62828", lw=2.2)
    rl, rh = s * tr.range_low, s * tr.range_high
    for y, lab, col in ((rl, "range low (1.0)", "#6a1b9a"), (rh, "range high (0) = target", "#1565c0"),
                        ((rl + rh) / 2, "0.5 equilibrium", "#999"), (rh - 0.62 * (rh - rl), "0.62 OTE", "#ef6c00"),
                        (rh - 0.79 * (rh - rl), "0.79", "#ef6c00"), (s * tr.px, "entry (M15 FVG 50%)", "#000"),
                        (s * tr.sl, "stop", "#c62828")):
        ax.axhline(y, color=col, lw=1, ls="--")
        ax.text(pd.to_datetime(t0, unit="s"), y, lab, fontsize=8, color=col, va="bottom")
    for tt, lab in ((tr.t_bos, "H4 BOS"), (tr.t_zone, "OTE touch"), (tr.t_mss, "M15 MSS"), (tr.t_entry, "fill")):
        ax.axvline(pd.to_datetime(tt, unit="s"), color="#888", lw=0.8)
        ax.text(pd.to_datetime(tt, unit="s"), ax.get_ylim()[1], lab, fontsize=8, rotation=90, va="top")
    ax.set_title(f"check {i}: {SYM} {'BUY' if s == 1 else 'SELL'} {pd.to_datetime(tr.t_entry, unit='s'):%Y-%m-%d %H:%M} UTC  ->  {tr.R:+.2f}R  (M15 candles)")
    fig.tight_layout()
    fig.savefig(OUT / f"check_{i:02d}.png", dpi=90)
    plt.close(fig)


def main():
    import topdown
    _, trades = topdown.run_sym(SYM)
    tr = pd.DataFrame(trades)
    tr = tr[(tr.htf == "H4") & (tr.ltf == "M15") & (tr.zone == "OTE") & (tr.entry == "LTF_FVG_CE") & (tr.target == "RH")]
    pick = pd.concat([tr[(tr.dir == 1) & (tr.R > 0)].sample(3, random_state=1), tr[(tr.dir == 1) & (tr.R < 0)].sample(2, random_state=1),
                      tr[(tr.dir == -1) & (tr.R > 0)].sample(3, random_state=1), tr[(tr.dir == -1) & (tr.R < 0)].sample(2, random_state=1)])
    from bensim.backtest import load_m5
    raw = load_m5(SYM, "2021-04-01", "2026-12-31")
    good = 0
    for i, (_, x) in enumerate(pick.iterrows(), 1):
        m = raw if x.dir == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        H4, M15 = bars_tf(m, 14400), bars_tf(m, 900)
        ok, notes, idx = check(x, m, H4, M15)
        good += ok
        print(f"\n#{i} {SYM} {'BUY' if x.dir == 1 else 'SELL'}  {'OK' if ok else 'MISMATCH'}  (prices shown as real prices)")
        for n_ in notes:
            print("   " + n_)
        chart(i, x, m, M15, idx)
    print(f"\n{good}/10 trades reproduced step by step by the separate checker; charts: reports/round30/check_01..10.png")


if __name__ == "__main__":
    main()
